"""config store 的并发正确性 —— 缺陷 E 的回归护栏。

根因不是"缺锁"：`save_assistant_config` 本来就是 tmp + os.replace 原子写。
真正的问题是**多个线程各持一份内存副本、写回时整份覆盖**：
  - WebUI PUT 每次 load 一份新的再全量写回（HTTP 线程池 max_workers=20）
  - scheduler 摘要后写的是它自己那份 self._config（线程池 max_workers=3）
  - agent 工具还有 7 处同样的 load→改→save（router 消息线程）
所以两个方向都会丢：摘要线程用旧副本覆盖用户刚改的配置；或 WebUI 换掉
self._config 后，正在跑的 _generate_digest 把记忆写在被丢弃的旧对象上。
`mutate_config` 的"锁内重读磁盘再改"才是修复，锁只防文件撕裂。
"""
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import src.assistant.config as config_mod
from src.assistant.config import (
    AssistantConfig,
    DigestChat,
    DigestGroup,
    MEMORY_MAX_CHARS,
    cas_digest_group_memory,
    group_memory_budget,
    memory_char_budget,
    mutate_config,
    truncate_memory,
    update_digest_group_memory,
)


def make_group(gid="dg_001", name="测试组", memory="", rev=0):
    return DigestGroup(id=gid, name=name,
                       chats=[DigestChat(chat_id="a@chatroom", name=name)],
                       memory=memory, memory_rev=rev)


class TestConfigStore(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config_mod.CONFIG_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"
        cfg = AssistantConfig(digest_groups=[make_group()])
        config_mod.save_assistant_config(cfg)

    def tearDown(self):
        config_mod.CONFIG_PATH = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def disk_group(self, gid="dg_001"):
        for g in config_mod.load_assistant_config().digest_groups:
            if g.id == gid:
                return g
        return None

    # ── mutate_config ────────────────────────────────────────────

    def test_mutate_config_rereads_from_disk(self):
        """写入基底必须是磁盘最新值，不是调用方手里的副本。"""
        stale = config_mod.load_assistant_config()
        stale.digest_groups[0].name = "过期副本改的名字"

        # 别人先写了磁盘
        mutate_config(lambda c: setattr(c.digest_groups[0], "name", "别人改的名字"))
        # 用"过期副本"的语义去改另一个字段
        mutate_config(lambda c: setattr(c.digest_groups[0], "lookback_hours", 12))

        g = self.disk_group()
        self.assertEqual(g.name, "别人改的名字", "过期副本不得覆盖别人的写入")
        self.assertEqual(g.lookback_hours, 12)

    def test_mutate_config_returns_saved_object(self):
        out = mutate_config(lambda c: setattr(c.digest_groups[0], "name", "新名字"))
        self.assertEqual(out.digest_groups[0].name, "新名字")
        self.assertEqual(self.disk_group().name, "新名字")

    def test_concurrent_mutators_lose_nothing(self):
        """20 线程并发追加，最终必须正好 20 个 —— 直接复现丢更新。"""
        n = 20
        barrier = threading.Barrier(n)
        errors = []
        barrier_timeouts = []

        def worker(i):
            try:
                # barrier 只是为了尽量制造真实竞争；超时不代表产品有问题
                # （CPU 争用时线程没能同时到达），此时测试依然有效。
                barrier.wait(timeout=60)
            except threading.BrokenBarrierError:
                barrier_timeouts.append(i)
            try:
                mutate_config(
                    lambda c: c.digest_groups[0].chats.append(
                        DigestChat(chat_id=f"c{i}@chatroom", name=f"C{i}")))
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], "并发写入抛了异常")
        chats = self.disk_group().chats
        self.assertEqual(len(chats), n + 1, f"丢了 {n + 1 - len(chats)} 个写入")
        ids = sorted(c.chat_id for c in chats)
        self.assertEqual(ids, sorted(["a@chatroom"] + [f"c{i}@chatroom" for i in range(n)]))

    def test_lock_is_reentrant(self):
        """mutator 内部再调 load_assistant_config 不能死锁。"""
        seen = {}

        def _m(cfg):
            seen["inner"] = config_mod.load_assistant_config().digest_groups[0].name
            cfg.digest_groups[0].name = "改过了"

        mutate_config(_m)
        self.assertEqual(seen["inner"], "测试组")
        self.assertEqual(self.disk_group().name, "改过了")

    def test_mutator_exception_does_not_persist(self):
        """校验失败时绝不能落盘（server.py 的 PUT 依赖这个语义）。"""
        def _m(cfg):
            cfg.digest_groups[0].name = "改了一半"
            raise ValueError("cron 表达式不合法")

        with self.assertRaises(ValueError):
            mutate_config(_m)
        self.assertEqual(self.disk_group().name, "测试组", "抛异常前不得落盘")

    # ── update_digest_group_memory ───────────────────────────────

    def test_memory_write_survives_config_object_replacement(self):
        """缺陷 E 的核心场景复现。

        scheduler 提交线程池时捕获的是旧 config 里的 dg 对象；期间 WebUI
        保存过配置（换了名字），旧写法 dg.memory=... + save(self._config)
        会把记忆写在已被丢弃的对象上 → 静默丢失。按 id 写就不会。
        """
        stale_dg = config_mod.load_assistant_config().digest_groups[0]
        self.assertEqual(stale_dg.name, "测试组")

        # WebUI 期间改了名字并落盘
        mutate_config(lambda c: setattr(c.digest_groups[0], "name", "用户改的新名字"))

        # 摘要线程此时才写记忆（它手里还是 stale_dg）
        update_digest_group_memory(stale_dg.id, "本次摘要生成的记忆")

        g = self.disk_group()
        self.assertEqual(g.memory, "本次摘要生成的记忆", "记忆不能丢")
        self.assertEqual(g.name, "用户改的新名字", "用户的改动也不能被覆盖")

    def test_memory_truncated_to_limit(self):
        budget = group_memory_budget(self.disk_group())
        self.assertEqual(budget, MEMORY_MAX_CHARS, "单会话分组的额度就是基准值")
        update_digest_group_memory("dg_001", "长" * (budget + 500))
        self.assertEqual(len(self.disk_group().memory), budget)

    def test_memory_budget_scales_with_chat_count(self):
        """一份记忆服务全组：会话越多额度越大，否则多会话组必然整段丢会话。"""
        def _add(cfg):
            cfg.digest_groups[0].chats.extend(
                DigestChat(chat_id=f"c{i}@chatroom", name=f"会话{i}")
                for i in range(4))
        mutate_config(_add)
        self.assertEqual(len(self.disk_group().chats), 5)
        update_digest_group_memory("dg_001", "长" * 5000)
        self.assertEqual(len(self.disk_group().memory), memory_char_budget(5))
        self.assertGreater(memory_char_budget(5), MEMORY_MAX_CHARS)

    def test_memory_rev_increments_each_write(self):
        self.assertEqual(self.disk_group().memory_rev, 0)
        self.assertEqual(update_digest_group_memory("dg_001", "一"), 1)
        self.assertEqual(update_digest_group_memory("dg_001", "二"), 2)
        self.assertEqual(self.disk_group().memory_rev, 2)

    def test_unknown_group_raises_keyerror(self):
        with self.assertRaises(KeyError):
            update_digest_group_memory("dg_999", "x")
        # 失败不得留下半成品写入
        self.assertEqual(self.disk_group().memory, "")

    def test_none_memory_does_not_crash(self):
        update_digest_group_memory("dg_001", None)
        self.assertEqual(self.disk_group().memory, "")

    # ── cas_digest_group_memory ──────────────────────────────────

    def test_cas_success_with_matching_rev(self):
        ok, rev, mem = cas_digest_group_memory("dg_001", "手工编辑", expect_rev=0)
        self.assertTrue(ok)
        self.assertEqual(rev, 1)
        self.assertEqual(mem, "手工编辑")
        self.assertEqual(self.disk_group().memory, "手工编辑")

    def test_cas_conflict_returns_disk_value(self):
        """后台先写了 → 手工编辑带旧 rev 必须失败并拿到最新值。"""
        update_digest_group_memory("dg_001", "后台写的")
        ok, rev, mem = cas_digest_group_memory("dg_001", "手工编辑", expect_rev=0)
        self.assertFalse(ok)
        self.assertEqual(rev, 1)
        self.assertEqual(mem, "后台写的", "要把磁盘最新值回给前端回填")
        self.assertEqual(self.disk_group().memory, "后台写的", "冲突时不得覆盖")

    def test_cas_retry_with_fresh_rev_succeeds(self):
        update_digest_group_memory("dg_001", "后台写的")
        ok, rev, mem = cas_digest_group_memory("dg_001", "手工改", expect_rev=1)
        self.assertTrue(ok)
        self.assertEqual(rev, 2)
        self.assertEqual(self.disk_group().memory, "手工改")

    def test_cas_unknown_group(self):
        ok, rev, mem = cas_digest_group_memory("dg_999", "x", expect_rev=0)
        self.assertFalse(ok)
        self.assertEqual(rev, -1, "-1 是「分组不存在」的哨兵值，区别于 rev 冲突")

    def test_cas_truncates(self):
        cas_digest_group_memory("dg_001", "长" * (MEMORY_MAX_CHARS + 10), expect_rev=0)
        self.assertEqual(len(self.disk_group().memory), MEMORY_MAX_CHARS)

    # ── 与 WebUI 批量 PUT 的组合 ─────────────────────────────────

    def test_bulk_put_does_not_clobber_background_memory(self):
        """模拟 server.py 的 _apply 全流程。"""
        from src.assistant.config import _dict_to_config, merge_digest_groups

        # 前端 GET 到的副本（memory 是旧值）
        incoming_raw = [{
            "id": "dg_001", "name": "前端改的名字",
            "chats": [{"chat_id": "a@chatroom", "name": "测试组", "enabled": True}],
            "cron_expr": "0 9 * * *", "lookback_hours": 12,
            "memory": "前端手里的过期记忆", "memory_rev": 0,
        }]
        # 期间后台写了记忆
        update_digest_group_memory("dg_001", "后台刚生成的记忆")

        def _apply(cfg):
            incoming = _dict_to_config({"digest_groups": incoming_raw}).digest_groups
            cfg.digest_groups = merge_digest_groups(cfg.digest_groups, incoming)

        mutate_config(_apply)
        g = self.disk_group()
        self.assertEqual(g.memory, "后台刚生成的记忆", "批量 PUT 不得回滚记忆")
        self.assertEqual(g.memory_rev, 1)
        self.assertEqual(g.name, "前端改的名字", "其他字段照常更新")
        self.assertEqual(g.lookback_hours, 12)


class TestSchedulerFreshReload(unittest.TestCase):
    """scheduler 必须在运行时从磁盘重读分组。

    这是缺陷 E 在 scheduler 侧的修复：任务提交进线程池时捕获的 DigestGroup
    属于当时的 config 副本，若它在队列里等了几分钟、期间 WebUI 保存过配置，
    这个对象就成了幽灵 —— 拿它写记忆等于写进一个已被丢弃的对象。改成传
    group_id + 运行时重读后，这条路从物理上不存在了。
    """

    def setUp(self):
        from src.assistant import scheduler as sched_mod
        from src.assistant.scheduler import DigestScheduler
        self.sched_mod = sched_mod
        self._tmp = tempfile.mkdtemp()
        self._orig_cfg = config_mod.CONFIG_PATH
        self._orig_state = sched_mod._STATE_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"
        sched_mod._STATE_PATH = str(Path(self._tmp) / "scheduler_state.json")
        config_mod.save_assistant_config(
            AssistantConfig(digest_groups=[make_group(memory="磁盘上的记忆")]))
        self.cfg = config_mod.load_assistant_config()
        self.task_center = MagicMock()
        self.sched = DigestScheduler(self.cfg, MagicMock(), summarizer=MagicMock(),
                                     store=MagicMock(), task_center=self.task_center)

    def tearDown(self):
        config_mod.CONFIG_PATH = self._orig_cfg
        self.sched_mod._STATE_PATH = self._orig_state
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_load_group_fresh_returns_disk_copy_not_stale_object(self):
        stale = DigestGroup(id="dg_001", name="过期名字", memory="过期记忆")
        fresh = self.sched._load_group_fresh(stale)
        self.assertIsNot(fresh, stale)
        self.assertEqual(fresh.memory, "磁盘上的记忆")
        self.assertEqual(fresh.name, "测试组")

    def test_load_group_fresh_picks_up_concurrent_edits(self):
        """提交线程池之后磁盘被改过 → 运行时必须看到新值。"""
        mutate_config(lambda c: setattr(c.digest_groups[0], "lookback_hours", 48))
        stale = config_mod.load_assistant_config().digest_groups[0]
        stale.lookback_hours = 6          # 模拟"手里的副本已过期"
        self.assertEqual(self.sched._load_group_fresh(stale).lookback_hours, 48)

    def test_temp_group_returned_as_is(self):
        """手动触发未保存的临时分组（id 为空）不能去磁盘找。"""
        temp = DigestGroup(id="", name="临时组",
                           chats=[DigestChat(chat_id="t@chatroom", name="临时组")])
        self.assertIs(self.sched._load_group_fresh(temp), temp)

    def test_unknown_or_empty_id_returns_none(self):
        self.assertIsNone(self.sched._load_group_fresh_by_id("dg_999"))
        self.assertIsNone(self.sched._load_group_fresh_by_id(""))

    def test_run_in_pool_fails_task_when_group_deleted_while_queued(self):
        """分组在排队期间被删掉 → 必须 fail_task，不能拿着幽灵对象跑摘要。"""
        mutate_config(lambda c: c.digest_groups.clear())
        self.sched._generate_digest = MagicMock()
        self.sched._run_digest_in_pool("dg_001", task_id=5)
        self.sched._generate_digest.assert_not_called()
        self.task_center.fail_task.assert_called_once_with(5, error="分组已被删除")

    def test_run_in_pool_reloads_before_generating(self):
        seen = {}
        self.sched._generate_digest = lambda dg, task_id=None: seen.update(
            memory=dg.memory, name=dg.name)
        self.sched._run_digest_in_pool("dg_001", task_id=7)
        self.assertEqual(seen["memory"], "磁盘上的记忆")
        self.assertEqual(seen["name"], "测试组")


class TestMemoryBudgetAndTruncation(unittest.TestCase):
    """记忆额度与截断的纯函数部分。"""

    def test_budget_scales_then_caps(self):
        self.assertEqual(memory_char_budget(1), 2000)
        self.assertEqual(memory_char_budget(2), 2300)
        self.assertEqual(memory_char_budget(5), 3200)
        self.assertEqual(memory_char_budget(8), 4000, "封顶")
        self.assertEqual(memory_char_budget(50), 4000)
        self.assertEqual(memory_char_budget(0), 2000, "0 按 1 处理")

    def test_group_budget_counts_only_summarizable_chats(self):
        """口径必须与 scheduler 拼 prompt 时的会话列表一致。

        两边不一致的话，prompt 里承诺的字数和落盘时的截断位置就是两个数，
        LLM 按要求写满却被切掉一块。
        """
        g = DigestGroup(id="x", name="n", chats=[
            DigestChat(chat_id="a@chatroom", name="a", enabled=True),
            DigestChat(chat_id="b@chatroom", name="b", enabled=False),
            DigestChat(chat_id="", name="没绑上"),
            DigestChat(chat_id="c@chatroom", name="c", enabled=True),
        ])
        self.assertEqual(group_memory_budget(g), memory_char_budget(2))

    def test_noop_when_under_budget(self):
        self.assertEqual(truncate_memory("短记忆", 2000), "短记忆")
        self.assertEqual(truncate_memory(None, 2000), "")
        self.assertEqual(truncate_memory("", 2000), "")

    def test_drops_incomplete_chat_block(self):
        """盲切 [:budget] 的后果在生产数据里能看到：dg_003 的记忆正好停在
        "\\n\\n#### 4."，留下一个只有标题没有内容的残块。"""
        text = ("### 甲\n" + "a" * 800
                + "\n### 乙\n" + "b" * 800
                + "\n### 丙\n" + "c" * 800)
        self.assertEqual(len(text), 2420)
        out = truncate_memory(text, 2000)
        self.assertIn("### 甲", out)
        self.assertIn("### 乙", out)
        self.assertNotIn("### 丙", out, "写不完的那块要整块丢掉，别留半个标题")
        self.assertTrue(out.endswith("b" * 50))
        self.assertEqual(len(out), 1613)

    def test_falls_back_to_blank_line(self):
        text = ("段落一 " + "a" * 900 + "\n\n"
                + "段落二 " + "b" * 900 + "\n\n"
                + "段落三 " + "c" * 900)
        out = truncate_memory(text, 2000)
        self.assertIn("段落二", out)
        self.assertNotIn("段落三", out)
        self.assertEqual(len(out), 1810)

    def test_hard_cuts_when_no_usable_boundary(self):
        self.assertEqual(len(truncate_memory("长" * 3000, 2000)), 2000)

    def test_ignores_boundary_near_start(self):
        """边界太靠前说明这份记忆没有分块结构，宁可硬切也别丢掉九成内容。"""
        text = "### 甲\n短\n\n" + "b" * 3000
        self.assertEqual(len(truncate_memory(text, 2000)), 2000)


if __name__ == "__main__":
    unittest.main()
