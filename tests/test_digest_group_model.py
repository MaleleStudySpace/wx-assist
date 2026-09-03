"""分组模型的数据迁移、合并与校验。

线上 3 条 digest_groups 各自累积了 1495 / 913 / 2000 字符的摘要记忆
（共 4408 字符），是 LLM 逐次摘要产生的、**无法重新生成**的数据。
迁移一旦丢字符就是永久损失，所以这里逐字符断言。
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import src.assistant.config as config_mod
from src.assistant.config import (
    AssistantConfig,
    DigestChat,
    DigestGroup,
    GroupProfile,
    merge_digest_groups,
    validate_digest_groups,
)

# 改动前从生产 data/assistant_config.json 记录的基线
LIVE_MEMORIES = {
    "聚沙成塔": 1495,
    "一手行口冲量实时更新群①": 913,
    "冲量中介群": 2000,
}
LIVE_TOTAL = 4408


def legacy_item(name, chat_id, memory, **extra):
    """旧 shape：一个会话一条配置。"""
    item = {
        "chat_id": chat_id,
        "group_name": name,
        "schedule": ["21:00"],
        "cron_expr": "0 21 * * *",
        "lookback_hours": 25,
        "enabled": True,
        "profile": None,
        "memory": memory,
        "memory_enabled": True,
        "unread_only": False,
        "push_target": "",
    }
    item.update(extra)
    return item


class ConfigPathIsolated(unittest.TestCase):
    """把 CONFIG_PATH 指到临时目录，绝不碰生产配置。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config_mod.CONFIG_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"

    def tearDown(self):
        config_mod.CONFIG_PATH = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write_raw(self, data):
        config_mod.CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestLegacyMigration(ConfigPathIsolated):

    def test_legacy_group_becomes_single_chat_group(self):
        self.write_raw({"digest_groups": [
            legacy_item("聚沙成塔", "56925293326@chatroom", "记忆A")]})
        dg = config_mod.load_assistant_config().digest_groups[0]
        self.assertRegex(dg.id, r"^dg_\d{3}$")
        self.assertEqual(dg.name, "聚沙成塔")
        self.assertEqual(len(dg.chats), 1)
        self.assertEqual(dg.chats[0].chat_id, "56925293326@chatroom")
        self.assertEqual(dg.chats[0].name, "聚沙成塔")
        self.assertTrue(dg.chats[0].enabled)
        self.assertEqual(dg.memory, "记忆A")
        self.assertEqual(dg.memory_rev, 0)
        self.assertEqual(dg.cron_expr, "0 21 * * *")
        self.assertEqual(dg.lookback_hours, 25)

    def test_migration_preserves_all_three_live_memories(self):
        """4408 字符逐字符相等 —— 这是不可再生数据。"""
        self.write_raw({"digest_groups": [
            legacy_item(name, f"{i}@chatroom", "记" * n)
            for i, (name, n) in enumerate(LIVE_MEMORIES.items())]})
        groups = config_mod.load_assistant_config().digest_groups
        self.assertEqual(len(groups), 3)
        for g, (name, n) in zip(groups, LIVE_MEMORIES.items()):
            self.assertEqual(g.name, name)
            self.assertEqual(len(g.memory), n)
            self.assertEqual(g.memory, "记" * n)
        self.assertEqual(sum(len(g.memory) for g in groups), LIVE_TOTAL)

    def test_migration_is_idempotent(self):
        """load → save → load 三次必须完全一致，否则每次重启都在改数据。"""
        self.write_raw({"digest_groups": [
            legacy_item("A", "a@chatroom", "记忆A"),
            legacy_item("B", "b@chatroom", "记忆B")]})

        def snap():
            return [(g.id, g.name, g.memory, g.memory_rev, g.cron_expr,
                     g.lookback_hours, g.lookback_mode, g.enabled,
                     [(c.chat_id, c.name, c.enabled) for c in g.chats])
                    for g in config_mod.load_assistant_config().digest_groups]

        first = snap()
        config_mod.save_assistant_config(config_mod.load_assistant_config())
        second = snap()
        config_mod.save_assistant_config(config_mod.load_assistant_config())
        third = snap()
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_ids_are_unique_and_stable_across_reload(self):
        self.write_raw({"digest_groups": [
            legacy_item("A", "a@chatroom", ""), legacy_item("B", "b@chatroom", "")]})
        ids1 = [g.id for g in config_mod.load_assistant_config().digest_groups]
        self.assertEqual(len(set(ids1)), 2)
        config_mod.save_assistant_config(config_mod.load_assistant_config())
        ids2 = [g.id for g in config_mod.load_assistant_config().digest_groups]
        self.assertEqual(ids1, ids2, "id 必须在落盘后稳定，否则 state key 会再次失配")

    def test_legacy_group_without_chat_id_yields_empty_chats(self):
        """agent 工具建的旧组只有 group_name（chat_id 恒空）→ 不能凭空造会话。"""
        self.write_raw({"digest_groups": [
            legacy_item("无绑定的组", "", "旧记忆")]})
        dg = config_mod.load_assistant_config().digest_groups[0]
        self.assertEqual(dg.chats, [])
        self.assertEqual(dg.name, "无绑定的组")
        self.assertEqual(dg.memory, "旧记忆", "即便没有会话，记忆也不能丢")

    def test_duplicate_chat_across_legacy_groups_keeps_first(self):
        self.write_raw({"digest_groups": [
            legacy_item("先来的", "dup@chatroom", "m1"),
            legacy_item("后来的", "dup@chatroom", "m2")]})
        groups = config_mod.load_assistant_config().digest_groups
        self.assertEqual(len(groups[0].chats), 1)
        self.assertEqual(groups[1].chats, [], "一个会话只能属于一个组")
        self.assertEqual(groups[1].memory, "m2", "记忆仍要保留")

    def test_dirty_values_do_not_raise(self):
        """_parse_digest_groups 绝不能抛异常 —— 一抛就会触发默认配置覆盖整份文件。"""
        self.write_raw({"digest_groups": [
            {"id": "dg_001", "name": "脏数据组",
             "chats": [{"chat_id": "a@chatroom", "enabled": "yes"}, "不是字典", None],
             "lookback_hours": "不是数字", "memory_rev": None,
             "memory": 12345, "profile": "不是字典"},
            "整个条目都不是字典",
            None,
        ]})
        cfg = config_mod.load_assistant_config()   # 不应抛
        self.assertEqual(len(cfg.digest_groups), 1)
        dg = cfg.digest_groups[0]
        self.assertEqual(dg.lookback_hours, 6, "脏值回落默认")
        self.assertEqual(dg.memory_rev, 0)
        self.assertEqual(len(dg.chats), 1)
        self.assertIsNone(dg.profile)

    def test_new_shape_round_trip_preserves_all_fields(self):
        cfg = config_mod.load_assistant_config()
        cfg.digest_groups.append(DigestGroup(
            id="dg_007", name="多会话组",
            chats=[DigestChat("a@chatroom", "甲", True),
                   DigestChat("b@chatroom", "乙", False)],
            schedule=["09:00"], cron_expr="0 9 * * 1-5",
            lookback_hours=12, lookback_mode="auto", enabled=True,
            profile=GroupProfile(style="完整复盘", custom_prompt="保留金句"),
            memory="组级共享记忆", memory_rev=3,
            memory_enabled=False, unread_only=True, push_target="ilink"))
        config_mod.save_assistant_config(cfg)
        dg = config_mod.load_assistant_config().digest_groups[0]
        self.assertEqual(dg.id, "dg_007")
        self.assertEqual(dg.name, "多会话组")
        self.assertEqual([(c.chat_id, c.name, c.enabled) for c in dg.chats],
                         [("a@chatroom", "甲", True), ("b@chatroom", "乙", False)])
        self.assertEqual(dg.cron_expr, "0 9 * * 1-5")
        self.assertEqual(dg.lookback_mode, "auto")
        self.assertEqual(dg.memory, "组级共享记忆")
        self.assertEqual(dg.memory_rev, 3)
        self.assertFalse(dg.memory_enabled)
        self.assertTrue(dg.unread_only)
        self.assertEqual(dg.push_target, "ilink")
        self.assertEqual(dg.profile.style, "完整复盘")

    def test_legacy_keys_are_not_written_back(self):
        self.write_raw({"digest_groups": [legacy_item("A", "a@chatroom", "m")]})
        config_mod.save_assistant_config(config_mod.load_assistant_config())
        raw = json.loads(config_mod.CONFIG_PATH.read_text(encoding="utf-8"))
        item = raw["digest_groups"][0]
        self.assertNotIn("chat_id", item)
        self.assertNotIn("group_name", item)
        self.assertIn("chats", item)
        self.assertIn("id", item)


class TestMergeDigestGroups(unittest.TestCase):

    def test_disk_memory_wins_over_stale_incoming(self):
        """浏览器手里的 config 可能是几分钟前 GET 的，其间后台已写过记忆。"""
        disk = [DigestGroup(id="dg_001", name="A", memory="后台刚写的新记忆", memory_rev=7)]
        incoming = [DigestGroup(id="dg_001", name="A改过名字", memory="过期记忆", memory_rev=2)]
        out = merge_digest_groups(disk, incoming)
        self.assertEqual(out[0].memory, "后台刚写的新记忆")
        self.assertEqual(out[0].memory_rev, 7)
        self.assertEqual(out[0].name, "A改过名字", "其他字段仍取前端提交值")

    def test_new_group_gets_id_and_zero_rev(self):
        disk = [DigestGroup(id="dg_001", name="A")]
        incoming = [DigestGroup(id="dg_001", name="A"),
                    DigestGroup(id="", name="新建的", chats=[DigestChat("x@chatroom", "X")])]
        out = merge_digest_groups(disk, incoming)
        self.assertEqual(len(out), 2)
        self.assertRegex(out[1].id, r"^dg_\d{3}$")
        self.assertNotEqual(out[1].id, "dg_001")
        self.assertEqual(out[1].memory_rev, 0)

    def test_group_absent_from_incoming_is_deleted(self):
        disk = [DigestGroup(id="dg_001", name="A"), DigestGroup(id="dg_002", name="B")]
        out = merge_digest_groups(disk, [DigestGroup(id="dg_002", name="B")])
        self.assertEqual([g.id for g in out], ["dg_002"])

    def test_incoming_ids_do_not_collide_after_deletion(self):
        """删掉 dg_001 后新建的组不能复用 dg_001，否则旧 state key 会串。"""
        disk = [DigestGroup(id="dg_001", name="A"), DigestGroup(id="dg_002", name="B")]
        incoming = [DigestGroup(id="dg_002", name="B"),
                    DigestGroup(id="", name="C", chats=[DigestChat("c@chatroom", "C")])]
        out = merge_digest_groups(disk, incoming)
        ids = [g.id for g in out]
        self.assertEqual(len(set(ids)), len(ids))


class TestValidateDigestGroups(unittest.TestCase):

    def test_valid(self):
        self.assertEqual(validate_digest_groups([
            {"name": "A", "chats": [{"chat_id": "a@chatroom"}]},
            {"name": "B", "chats": [{"chat_id": "b@chatroom"}]},
        ]), "")

    def test_empty_name_rejected(self):
        err = validate_digest_groups([{"name": "  ", "chats": [{"chat_id": "a"}]}])
        self.assertIn("名称不能为空", err)

    def test_no_chats_rejected(self):
        err = validate_digest_groups([{"name": "A", "chats": []}])
        self.assertIn("至少要选择一个会话", err)

    def test_blank_chat_id_does_not_count(self):
        err = validate_digest_groups([{"name": "A", "chats": [{"chat_id": "  "}]}])
        self.assertIn("至少要选择一个会话", err)

    def test_duplicate_chat_across_groups_rejected(self):
        err = validate_digest_groups([
            {"name": "甲组", "chats": [{"chat_id": "dup@chatroom"}]},
            {"name": "乙组", "chats": [{"chat_id": "dup@chatroom"}]},
        ])
        self.assertIn("甲组", err)
        self.assertIn("乙组", err)
        self.assertIn("只能属于一个分组", err)

    def test_duplicate_within_one_group_rejected(self):
        err = validate_digest_groups([
            {"name": "甲组", "chats": [{"chat_id": "d"}, {"chat_id": "d"}]}])
        self.assertIn("甲组", err)

    def test_legacy_shape_still_validates(self):
        """旧前端提交 chat_id/group_name 时也要能校验，不能直接放行。"""
        self.assertEqual(validate_digest_groups(
            [{"group_name": "旧组", "chat_id": "a@chatroom"}]), "")
        self.assertIn("至少要选择一个会话",
                      validate_digest_groups([{"group_name": "旧组"}]))

    def test_malformed_input(self):
        self.assertIn("格式不正确", validate_digest_groups("不是列表"))
        self.assertIn("格式不正确", validate_digest_groups(["不是字典"]))


if __name__ == "__main__":
    unittest.main()
