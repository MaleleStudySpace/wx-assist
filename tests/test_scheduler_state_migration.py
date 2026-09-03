"""scheduler_state.json 的 key 迁移，以及它对启动补触发的影响。

不迁移的后果很具体：`last_triggered` 的 key 从 chat_id 变成 `dg:{id}` 后，
旧 key 全部失配 → `_catch_up_missed_crons` 认为每个组"从没触发过" →
在 3 小时窗口内把匹配过的 cron 全部补触发一轮。线上 3 个组就是升级后
立刻收到 3 条重复摘要推送。
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.assistant.config as config_mod
from src.assistant import scheduler as sched_mod
from src.assistant.config import AssistantConfig, DigestChat, DigestGroup
from src.assistant.scheduler import DigestScheduler, _migrate_state_keys

CHAT = "56925293326@chatroom"


def make_cfg(cron="0 21 * * *", gid="dg_001", name="聚沙成塔", chat_id=CHAT):
    return AssistantConfig(
        assistant_enabled=True,
        digest_groups=[DigestGroup(
            id=gid, name=name,
            chats=[DigestChat(chat_id=chat_id, name=name)] if chat_id else [],
            cron_expr=cron, enabled=True)],
    )


class TestMigrateStateKeysPure(unittest.TestCase):
    """纯函数层面：不碰磁盘。"""

    def test_chat_id_key_migrated_to_group_id(self):
        state = {CHAT: 1000.0}
        out, changed = _migrate_state_keys(state, make_cfg())
        self.assertTrue(changed)
        self.assertEqual(out["dg:dg_001"], 1000.0)
        self.assertNotIn(CHAT, out)

    def test_falls_back_to_group_name(self):
        """agent 工具建的旧组没有 chat_id，state 里存的是群名。"""
        state = {"聚沙成塔": 2000.0}
        out, changed = _migrate_state_keys(state, make_cfg(chat_id=""))
        self.assertTrue(changed)
        self.assertEqual(out["dg:dg_001"], 2000.0)

    def test_takes_max_when_several_legacy_keys_match(self):
        """取最近一次：拿更早的时间戳会误判成"错过了"而补触发。"""
        state = {CHAT: 1000.0, "聚沙成塔": 3000.0}
        out, _ = _migrate_state_keys(state, make_cfg())
        self.assertEqual(out["dg:dg_001"], 3000.0)

    def test_idempotent(self):
        cfg = make_cfg()
        state = {CHAT: 1000.0}
        first, changed1 = _migrate_state_keys(state, cfg)
        self.assertTrue(changed1)
        second, changed2 = _migrate_state_keys(dict(first), cfg)
        self.assertFalse(changed2, "第二次不应再改动")
        self.assertEqual(first, second)

    def test_oa_keys_untouched(self):
        state = {"oa:grp_001": 5000.0, CHAT: 1000.0}
        out, _ = _migrate_state_keys(state, make_cfg())
        self.assertEqual(out["oa:grp_001"], 5000.0)
        self.assertEqual(out["dg:dg_001"], 1000.0)

    def test_orphan_keys_dropped(self):
        """已删除分组的残留 key 要清掉，否则 state 文件只增不减。"""
        state = {"已删组@chatroom": 1.0, "某个旧群名": 2.0, "oa:grp_001": 3.0}
        out, changed = _migrate_state_keys(state, make_cfg())
        self.assertTrue(changed)
        self.assertEqual(set(out), {"oa:grp_001"})

    def test_no_legacy_match_creates_no_key(self):
        """全新安装：state 里没有对应记录时不该凭空造 key。

        造了会让 _catch_up_missed_crons 认为"最近触发过"，从而永久跳过
        本该补触发的那一次。
        """
        out, changed = _migrate_state_keys({}, make_cfg())
        self.assertEqual(out, {})
        self.assertFalse(changed)


class TestSchedulerStateMigration(unittest.TestCase):
    """走真实 DigestScheduler 构造路径，验证迁移落盘。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cfg_path = config_mod.CONFIG_PATH
        self._orig_state_path = sched_mod._STATE_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"
        sched_mod._STATE_PATH = str(Path(self._tmp) / "scheduler_state.json")

    def tearDown(self):
        config_mod.CONFIG_PATH = self._orig_cfg_path
        sched_mod._STATE_PATH = self._orig_state_path
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write_state(self, state):
        Path(sched_mod._STATE_PATH).write_text(json.dumps(state), encoding="utf-8")

    def read_state(self):
        return json.loads(Path(sched_mod._STATE_PATH).read_text(encoding="utf-8"))

    def make_scheduler(self, cfg):
        return DigestScheduler(cfg, MagicMock(), summarizer=MagicMock(),
                               store=MagicMock(), task_center=MagicMock())

    def test_migration_happens_on_construction_and_persists(self):
        now = time.time()
        self.write_state({CHAT: now - 600})
        s = self.make_scheduler(make_cfg())
        self.assertEqual(s._last_triggered["dg:dg_001"], now - 600)
        self.assertNotIn(CHAT, s._last_triggered)
        # 必须立刻落盘：进程若在 catch-up 之后、_tick 之前崩溃，
        # 下次启动读到的仍是旧 key，会再补触发一轮
        self.assertEqual(self.read_state()["dg:dg_001"], now - 600)

    def test_catchup_does_not_refire_after_migration(self):
        """核心断言：迁移后不得重复推送。"""
        now = time.time()
        self.write_state({CHAT: now - 600})      # 10 分钟前刚触发过
        s = self.make_scheduler(make_cfg(cron="* * * * *"))   # 每分钟都匹配
        s._pool = MagicMock()
        s._catch_up_missed_crons()
        s._pool.submit.assert_not_called()

    def test_catchup_still_fires_when_genuinely_missed(self):
        """反向护栏：别把补触发功能一起弄坏。"""
        self.write_state({})                      # 全新状态，从没触发过
        s = self.make_scheduler(make_cfg(cron="* * * * *"))
        s._pool = MagicMock()
        s._catch_up_missed_crons()
        self.assertEqual(s._pool.submit.call_count, 1)
        args = s._pool.submit.call_args.args
        # 绑定方法每次属性访问都是新对象，只能用 == 比较（比 __func__ 与 __self__）
        self.assertEqual(args[0], s._run_digest_in_pool)
        self.assertEqual(args[1], "dg_001", "必须按 group_id 提交，不再传对象")

    def test_catchup_fires_when_last_trigger_older_than_window(self):
        """last_ts 超出 3h 窗口时仍会补触发一次（既有行为，不能被迁移改坏）。

        cutoff 判定不会跳过它，而 _cron_missed_in_window 的扫描起点被 clamp 到
        now-3h，每分钟 cron 在这段窗口内必然匹配过。
        """
        self.write_state({CHAT: time.time() - 10 * 3600})
        s = self.make_scheduler(make_cfg(cron="* * * * *"))
        s._pool = MagicMock()
        s._catch_up_missed_crons()
        self.assertEqual(s._pool.submit.call_count, 1)

    def test_update_config_stamps_first_seen_group(self):
        """热更新时新建的组要打上 now，否则紧接着就会被当成"错过了一次"。"""
        self.write_state({})
        s = self.make_scheduler(make_cfg())
        self.assertNotIn("dg:dg_002", s._last_triggered)

        new_cfg = make_cfg()
        new_cfg.digest_groups.append(DigestGroup(
            id="dg_002", name="刚建的组",
            chats=[DigestChat(chat_id="b@chatroom", name="刚建的组")],
            cron_expr="* * * * *", enabled=True))
        before = time.time()
        s.update_config(new_cfg)
        self.assertGreaterEqual(s._last_triggered["dg:dg_002"], before)
        self.assertLessEqual(s._last_triggered["dg:dg_002"], time.time())

    def test_update_config_does_not_reset_existing_stamps(self):
        """已有记录不能被 setdefault 覆盖，否则防重触失效。"""
        self.write_state({CHAT: 12345.0})
        s = self.make_scheduler(make_cfg())
        s.update_config(make_cfg())
        self.assertEqual(s._last_triggered["dg:dg_001"], 12345.0)

    def test_new_group_not_caught_up_right_after_creation(self):
        """端到端：网页端新建一个每分钟的组 → 不该立刻补触发一次。"""
        self.write_state({})
        s = self.make_scheduler(make_cfg())
        new_cfg = make_cfg()
        new_cfg.digest_groups.append(DigestGroup(
            id="dg_002", name="刚建的组",
            chats=[DigestChat(chat_id="b@chatroom", name="刚建的组")],
            cron_expr="* * * * *", enabled=True))
        s.update_config(new_cfg)
        s._pool = MagicMock()
        s._catch_up_missed_crons()
        submitted = [c.args[1] for c in s._pool.submit.call_args_list]
        self.assertNotIn("dg_002", submitted)


if __name__ == "__main__":
    unittest.main()
