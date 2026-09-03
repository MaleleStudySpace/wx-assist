"""分组摘要管线端到端测试（fake store / summarizer / outbox）。

填补此前的空白：改造前 tests/ 里**没有任何测试调用过 `_generate_digest`**。
覆盖打包 / 降级 / 单会话三条路径、provider 真超限时的自动降级、
部分会话失败的容错、组级记忆按 id 落盘，以及推送三态。
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
from src.assistant.config import (
    AssistantConfig, DigestChat, DigestGroup, GroupProfile,
)
from src.assistant.scheduler import DigestScheduler
from src.summarize.errors import LLMContextOverflowError

TASK_ID = 11


def msg(text, sender="张三", ts=None):
    return {"sender_name": sender, "content": text, "msg_type": 1,
            "timestamp": ts or int(time.time())}


class FakeSummarizer:
    """记录每次调用；按 prompt 里出现的标记决定返回值或抛异常。"""

    _backend_name = "fake"
    model = "fake-model"

    def __init__(self, default="默认摘要正文", by_marker=None,
                 overflow_when_packed=False):
        self.default = default
        self.by_marker = by_marker or {}
        self.overflow_when_packed = overflow_when_packed
        self.digest_calls = []      # [{"system","user","timeout"}]
        self.memory_calls = []

    def _call_digest_api(self, system_prompt, messages, timeout=None):
        prompt = messages[0]["content"]
        self.digest_calls.append({"system": system_prompt, "user": prompt,
                                  "timeout": timeout})
        if self.overflow_when_packed and "=== [1]" in prompt:
            raise LLMContextOverflowError(
                "[DIGEST-API] LLM 返回空 choices（base_resp=2013: context window "
                "exceeds limit），prompt_tokens=299247",
                prompt_tokens=299247, status_code=2013)
        for marker, action in self.by_marker.items():
            if marker in prompt:
                if isinstance(action, Exception):
                    raise action
                return action
        return self.default

    def _call_chat_api(self, system_prompt, messages):
        self.memory_calls.append(messages[0]["content"])
        return "浓缩后的新记忆"

    @property
    def call_count(self):
        return len(self.digest_calls)


class FakeStore:
    def __init__(self, by_chat):
        self.by_chat = by_chat
        self.calls = []

    def get_messages_since(self, chat_id, since_ts, until_ts=None, limit=500):
        self.calls.append({"chat_id": chat_id, "limit": limit})
        return [dict(m) for m in self.by_chat.get(chat_id, [])]


class FakeOutbox:
    def __init__(self):
        self.added = []
        self.push_results = []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return 100 + len(self.added)

    def update_push_result(self, nid, channel, status, error):
        self.push_results.append({"nid": nid, "channel": channel,
                                  "status": status, "error": error})

    @property
    def last_content(self):
        return json.loads(self.added[-1]["content"])


def make_group(chats, **kwargs):
    kwargs.setdefault("id", "dg_001")
    kwargs.setdefault("name", "测试分组")
    kwargs.setdefault("memory_enabled", False)   # 默认关掉，需要的用例单独打开
    return DigestGroup(
        chats=[DigestChat(chat_id=c, name=c.split("@")[0]) for c in chats],
        **kwargs)


class PipelineBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cfg = config_mod.CONFIG_PATH
        self._orig_state = sched_mod._STATE_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"
        sched_mod._STATE_PATH = str(Path(self._tmp) / "scheduler_state.json")

        patch.object(sched_mod, "log_llm_interaction", MagicMock()).start()
        patch("src.web.api_handlers.broadcast_event", MagicMock()).start()
        # 默认无绑定渠道 → 推送走 skipped 分支，不触碰真实 IM
        patch("src.im.targets.bound_push_targets", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        config_mod.CONFIG_PATH = self._orig_cfg
        sched_mod._STATE_PATH = self._orig_state
        shutil.rmtree(self._tmp, ignore_errors=True)

    def build(self, group, by_chat, summarizer=None, unread=None):
        """落盘配置并构造 scheduler。分组必须落盘，因为运行时会重读。"""
        cfg = AssistantConfig(assistant_enabled=True, digest_groups=[group])
        config_mod.save_assistant_config(cfg)
        self.cfg_on_disk = config_mod.load_assistant_config()

        store = FakeStore(by_chat)
        summarizer = summarizer or FakeSummarizer()
        outbox = FakeOutbox()
        tc = MagicMock()
        sched = DigestScheduler(self.cfg_on_disk, outbox, summarizer=summarizer,
                                store=store, task_center=tc)
        if unread is not None:
            sched._get_unread_map = lambda: unread
        return sched, summarizer, store, outbox, tc

    def run_digest(self, group, by_chat, **kw):
        sched, summarizer, store, outbox, tc = self.build(group, by_chat, **kw)
        sched._generate_digest(group, task_id=TASK_ID)
        return sched, summarizer, store, outbox, tc


THREE_CHATS = {
    "a@chatroom": [msg("甲群讨论了下周排期"), msg("甲群确认了预算")],
    "b@chatroom": [msg("乙群在聊新客户线索")],
    "c@chatroom": [msg("丙群发布了放假通知")],
}


def big_chats(names=("a", "b", "c"), n_msgs=15, content_len=300):
    """体积足够的夹具，用来撞穿预算下限触发降级。

    3 会话 × 15 条 × ~302 字符 ≈ 每会话 3940 估算 token、合计约 11940，
    既超过 MIN_AVAILABLE_TOKENS 地板的 1.2 倍，也超过 env kill switch
    被夹到 BUDGET_FLOOR 后的 available。
    """
    return {
        f"{x}@chatroom": [msg(f"{x}群" + "话" * content_len, ts=1000 + i)
                          for i in range(n_msgs)]
        for x in names
    }


class TestSingleChatUnchanged(PipelineBase):
    """迁移后线上 3 个分组都是单会话 → 升级当天必须零行为变化。"""

    def test_one_llm_call_and_no_packed_markers(self):
        g = make_group(["a@chatroom"])
        _, summ, _, outbox, tc = self.run_digest(g, THREE_CHATS)
        self.assertEqual(summ.call_count, 1)
        call = summ.digest_calls[0]
        self.assertNotIn("=== [1]", call["user"])
        self.assertNotIn("多会话打包输出契约", call["system"])
        self.assertIn("## 近期记忆", call["user"])
        self.assertIn("甲群讨论了下周排期", call["user"])
        self.assertEqual(outbox.last_content["digest_mode"], "single")
        self.assertFalse(outbox.last_content["degraded"])
        tc.complete_task.assert_called_once()

    def test_prompt_matches_legacy_builder_byte_for_byte(self):
        from src.assistant.digest import build_digest_prompt
        g = make_group(["a@chatroom"], memory="历史记忆内容")
        _, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        disk_group = config_mod.load_assistant_config().digest_groups[0]
        expected = build_digest_prompt(disk_group, THREE_CHATS["a@chatroom"])
        self.assertEqual(summ.digest_calls[0]["user"], expected)

    def test_only_that_chats_messages_are_fetched(self):
        g = make_group(["a@chatroom"])
        _, _, store, _, _ = self.run_digest(g, THREE_CHATS)
        self.assertEqual([c["chat_id"] for c in store.calls], ["a@chatroom"])


class TestPackedPath(PipelineBase):

    def test_one_llm_call_for_three_chats(self):
        g = make_group(["a@chatroom", "b@chatroom", "c@chatroom"])
        _, summ, store, outbox, tc = self.run_digest(g, THREE_CHATS)
        self.assertEqual(len(store.calls), 3)
        self.assertEqual(summ.call_count, 1, "打包必须是**一次** LLM 调用")
        user = summ.digest_calls[0]["user"]
        for marker in ("=== [1] a (2 条) ===", "=== [2] b (1 条) ===",
                       "=== [3] c (1 条) ==="):
            self.assertIn(marker, user)
        self.assertIn("## 待摘要的 3 个会话", user)
        self.assertEqual(outbox.last_content["digest_mode"], "packed")
        self.assertEqual(outbox.last_content["msg_count"], 4)
        tc.complete_task.assert_called_once()

    def test_output_contract_appended_to_system_prompt(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        _, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        self.assertIn("多会话打包输出契约", summ.digest_calls[0]["system"])
        self.assertIn("禁止跨会话合并话题", summ.digest_calls[0]["system"])

    def test_contract_appended_even_with_custom_prompt(self):
        """custom_prompt 完全替代摘要指令，但输出契约是结构约束，必须仍追加，
        否则多个会话会被混写成一篇。"""
        g = make_group(["a@chatroom", "b@chatroom"],
                       profile=GroupProfile(custom_prompt="只输出待办事项"))
        _, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        system = summ.digest_calls[0]["system"]
        self.assertTrue(system.startswith("只输出待办事项"))
        self.assertIn("多会话打包输出契约", system)

    def test_style_preset_and_contract_coexist(self):
        g = make_group(["a@chatroom", "b@chatroom"],
                       profile=GroupProfile(style="行动项优先"))
        _, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        system = summ.digest_calls[0]["system"]
        self.assertIn("## 摘要风格", system)
        self.assertIn("多会话打包输出契约", system)

    def test_outbox_records_group_id_and_chat_names(self):
        g = make_group(["a@chatroom", "b@chatroom", "c@chatroom"])
        _, _, _, outbox, _ = self.run_digest(g, THREE_CHATS)
        entry = outbox.added[-1]
        self.assertEqual(entry["chat_id"], "dg_001", "outbox 的 chat_id 存组 id")
        self.assertEqual(entry["group_name"], "测试分组")
        content = outbox.last_content
        self.assertEqual(content["group"], "测试分组")
        self.assertEqual(content["chats"], ["a", "b", "c"])
        self.assertIn("分组", content["display"])

    def test_group_memory_shared_across_chats(self):
        g = make_group(["a@chatroom", "b@chatroom"], memory="组级共享记忆")
        _, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        self.assertIn("组级共享记忆", summ.digest_calls[0]["user"])
        self.assertIn("本分组共用", summ.digest_calls[0]["user"])


class TestDegradedPath(PipelineBase):

    def tiny_budget(self):
        """直接 patch 预算函数。

        env 那条路受 BUDGET_FLOOR=10_000 与 MIN_AVAILABLE_TOKENS=2_000 两道地板
        保护，对小夹具太宽；env 解析与 kill switch 在 test_digest_budget.py
        单独覆盖，这里只测降级行为。
        """
        return patch.object(sched_mod, "digest_token_budget", return_value=1)

    def test_calls_llm_once_per_chat_and_no_merge_call(self):
        g = make_group(["a@chatroom", "b@chatroom", "c@chatroom"])
        with self.tiny_budget():
            _, summ, _, outbox, tc = self.run_digest(g, big_chats())
        self.assertEqual(summ.call_count, 3, "3 个会话 = 3 次调用，且没有第 4 次汇总")
        self.assertEqual(outbox.last_content["digest_mode"], "per_chat")
        self.assertTrue(outbox.last_content["degraded"])
        tc.complete_task.assert_called_once()

    def test_env_kill_switch_forces_degradation(self):
        """端到端验证运维开关：DIGEST_TOKEN_BUDGET=1 → 夹到下限 → 必然降级。"""
        g = make_group(["a@chatroom", "b@chatroom", "c@chatroom"])
        with patch.dict("os.environ", {"DIGEST_TOKEN_BUDGET": "1"}):
            _, summ, _, outbox, _ = self.run_digest(g, big_chats())
        self.assertEqual(summ.call_count, 3)
        self.assertEqual(outbox.last_content["digest_mode"], "per_chat")

    def test_concatenates_without_llm_merge(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        summ = FakeSummarizer(by_marker={"a群": "甲的摘要正文"})
        with self.tiny_budget():
            _, _, _, outbox, _ = self.run_digest(g, big_chats(("a", "b")),
                                                 summarizer=summ)
        digest = outbox.last_content["digest"]
        self.assertIn("## a", digest)
        self.assertIn("## b", digest)
        self.assertIn("甲的摘要正文", digest)
        self.assertIn("\n\n---\n\n", digest)
        # 每个会话的 prompt 里都不该出现别的会话的分节标记
        for call in summ.digest_calls:
            self.assertNotIn("=== [", call["user"])

    def test_per_chat_prompt_has_no_heading_instruction(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        with self.tiny_budget():
            _, summ, _, _, _ = self.run_digest(g, big_chats(("a", "b")))
        for call in summ.digest_calls:
            self.assertIn("不要输出会话名标题", call["system"])
            self.assertNotIn("多会话打包输出契约", call["system"])

    def test_display_marks_degraded(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        with self.tiny_budget():
            _, _, _, outbox, _ = self.run_digest(g, big_chats(("a", "b")))
        self.assertIn("已降级为逐会话", outbox.last_content["display"])

    def test_degradation_trims_each_chat_and_discloses(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        with self.tiny_budget():
            _, _, _, outbox, _ = self.run_digest(g, big_chats(("a", "b")))
        self.assertGreater(outbox.last_content["dropped_total"], 0)
        self.assertIn("已省略最早", outbox.last_content["digest"])

    def test_provider_overflow_degrades_instead_of_failing(self):
        """估算器保守但不保证；provider 真拒绝时就地降级，任务仍算成功。"""
        g = make_group(["a@chatroom", "b@chatroom", "c@chatroom"])
        summ = FakeSummarizer(overflow_when_packed=True,
                              by_marker={"甲群": "甲摘要", "乙群": "乙摘要",
                                         "丙群": "丙摘要"})
        _, _, _, outbox, tc = self.run_digest(g, THREE_CHATS, summarizer=summ)
        # 1 次打包（被拒）+ 3 次逐会话
        self.assertEqual(summ.call_count, 4)
        self.assertEqual(outbox.last_content["digest_mode"], "per_chat")
        digest = outbox.last_content["digest"]
        for name in ("甲摘要", "乙摘要", "丙摘要"):
            self.assertIn(name, digest)
        tc.complete_task.assert_called_once()
        tc.fail_task.assert_not_called()


class TestPartialFailure(PipelineBase):

    def tiny_budget(self):
        return patch.object(sched_mod, "digest_token_budget", return_value=1)

    def test_failed_chat_annotated_but_not_fatal(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        summ = FakeSummarizer(by_marker={"a群": "甲摘要",
                                         "b群": RuntimeError("boom")})
        with self.tiny_budget():
            _, _, _, outbox, tc = self.run_digest(g, big_chats(("a", "b")),
                                                  summarizer=summ)
        content = outbox.last_content
        self.assertIn("甲摘要", content["digest"])
        self.assertIn("（本次摘要失败：boom）", content["digest"])
        self.assertEqual(content["failed_chats"], ["b"])
        tc.complete_task.assert_called_once()
        tc.fail_task.assert_not_called()

    def test_all_chats_failed_fails_task_and_skips_memory(self):
        g = make_group(["a@chatroom", "b@chatroom"], memory_enabled=True,
                       memory="原有记忆")
        summ = FakeSummarizer(by_marker={"a群": RuntimeError("x"),
                                         "b群": RuntimeError("y")})
        with self.tiny_budget():
            _, _, _, outbox, tc = self.run_digest(g, big_chats(("a", "b")),
                                                  summarizer=summ)
        tc.fail_task.assert_called_once()
        tc.complete_task.assert_not_called()
        self.assertEqual(summ.memory_calls, [], "失败时不得把错误串浓缩进记忆")
        self.assertEqual(config_mod.load_assistant_config()
                         .digest_groups[0].memory, "原有记忆")
        self.assertTrue(outbox.last_content["digest"].startswith("摘要生成失败"))

    def test_chat_fetch_error_is_isolated(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        store_by_chat = dict(THREE_CHATS)
        sched, summ, store, outbox, tc = self.build(g, store_by_chat)
        real = store.get_messages_since

        def flaky(chat_id, since_ts, until_ts=None, limit=500):
            if chat_id == "b@chatroom":
                raise OSError("db locked")
            return real(chat_id, since_ts, until_ts, limit)

        store.get_messages_since = flaky
        sched._generate_digest(g, task_id=TASK_ID)
        self.assertEqual(summ.call_count, 1, "只剩一个会话有消息 → 走 single")
        tc.complete_task.assert_called_once()
        # 取消息失败的会话要暴露在 failed_chats 里，不能静默消失
        self.assertEqual(outbox.last_content["failed_chats"], ["b"])


class TestEmptyAndInvalid(PipelineBase):

    def test_all_chats_fetch_error_fails_instead_of_claiming_no_messages(self):
        """取消息全失败 ≠ 窗口内没消息，不能伪装成「无新消息」。"""
        g = make_group(["a@chatroom", "b@chatroom"])
        sched, summ, store, outbox, tc = self.build(g, THREE_CHATS)

        def always_fail(chat_id, since_ts, until_ts=None, limit=500):
            raise OSError("database is locked")

        store.get_messages_since = always_fail
        sched._generate_digest(g, task_id=TASK_ID)

        self.assertEqual(summ.call_count, 0)
        self.assertEqual(outbox.added, [], "不得写出误导性的「无新消息」记录")
        tc.complete_task.assert_not_called()
        tc.fail_task.assert_called_once()
        err = tc.fail_task.call_args.kwargs["error"]
        self.assertIn("取消息失败", err)
        self.assertIn("database is locked", err)
        self.assertIn("a", err)
        self.assertIn("b", err)

    def test_no_messages_still_writes_outbox(self):
        g = make_group(["a@chatroom", "b@chatroom"])
        _, summ, _, outbox, tc = self.run_digest(g, {"a@chatroom": [], "b@chatroom": []})
        self.assertEqual(summ.call_count, 0)
        content = outbox.last_content
        self.assertEqual(content["msg_count"], 0)
        self.assertEqual(content["digest_mode"], "none")
        self.assertEqual(content["chats"], ["a", "b"])
        self.assertIn("无新消息", content["digest"])
        tc.complete_task.assert_called_once_with(TASK_ID, result="无新内容")

    def test_all_noise_filtered_completes_without_outbox(self):
        g = make_group(["a@chatroom"])
        _, summ, _, outbox, tc = self.run_digest(
            g, {"a@chatroom": [msg("收到"), msg("好的")]})
        self.assertEqual(summ.call_count, 0)
        self.assertEqual(outbox.added, [], "过滤后无实质内容不写 outbox（既有语义）")
        tc.complete_task.assert_called_once_with(TASK_ID, result="无实质内容")

    def test_group_without_chats_fails_with_actionable_error(self):
        g = DigestGroup(id="dg_001", name="空分组", chats=[])
        _, summ, _, outbox, tc = self.run_digest(g, THREE_CHATS)
        self.assertEqual(summ.call_count, 0)
        self.assertEqual(outbox.added, [])
        tc.fail_task.assert_called_once_with(
            TASK_ID, error="分组没有可用会话，请在网页端重新绑定")

    def test_disabled_chat_is_skipped(self):
        g = DigestGroup(id="dg_001", name="测试分组", memory_enabled=False, chats=[
            DigestChat(chat_id="a@chatroom", name="a", enabled=True),
            DigestChat(chat_id="b@chatroom", name="b", enabled=False),
        ])
        _, summ, store, outbox, _ = self.run_digest(g, THREE_CHATS)
        self.assertEqual([c["chat_id"] for c in store.calls], ["a@chatroom"])
        self.assertEqual(summ.call_count, 1)
        self.assertEqual(outbox.last_content["chats"], ["a"])

    def test_chat_without_id_is_skipped(self):
        g = DigestGroup(id="dg_001", name="测试分组", memory_enabled=False, chats=[
            DigestChat(chat_id="", name="未绑定"),
            DigestChat(chat_id="a@chatroom", name="a"),
        ])
        _, _, store, _, _ = self.run_digest(g, THREE_CHATS)
        self.assertEqual([c["chat_id"] for c in store.calls], ["a@chatroom"])


class TestUnreadOnly(PipelineBase):

    def test_slices_each_chat_independently_and_reads_sessions_once(self):
        g = make_group(["a@chatroom", "b@chatroom"], unread_only=True)
        by_chat = {
            "a@chatroom": [msg("甲旧1"), msg("甲旧2"), msg("甲新1"), msg("甲新2"), msg("甲新3")],
            "b@chatroom": [msg("乙旧1"), msg("乙新1")],
        }
        calls = {"n": 0}
        sched, summ, store, outbox, tc = self.build(g, by_chat, unread={"a@chatroom": 3, "b@chatroom": 1})

        original = sched._get_unread_map

        def counting():
            calls["n"] += 1
            return original()

        sched._get_unread_map = counting
        sched._generate_digest(g, task_id=TASK_ID)

        self.assertEqual(calls["n"], 1, "WCDB session 要串行排队，只能拉一次")
        user = summ.digest_calls[0]["user"]
        self.assertIn("甲新1", user)
        self.assertNotIn("甲旧1", user)
        self.assertIn("乙新1", user)
        self.assertNotIn("乙旧1", user)
        self.assertIn("(3 条)", user)
        self.assertIn("(1 条)", user)

    def test_zero_unread_skips_that_chat(self):
        g = make_group(["a@chatroom", "b@chatroom"], unread_only=True)
        by_chat = {"a@chatroom": [msg("甲新1")], "b@chatroom": [msg("乙旧1")]}
        _, summ, _, _, tc = self.run_digest(
            g, by_chat, unread={"a@chatroom": 1, "b@chatroom": 0})
        user = summ.digest_calls[0]["user"]
        self.assertIn("甲新1", user)
        self.assertNotIn("乙旧1", user)


class TestMemoryIntegration(PipelineBase):

    def test_memory_written_by_id_survives_config_swap(self):
        """跑完摘要后把 scheduler._config 换成另一份对象，磁盘记忆仍应是新值。"""
        g = make_group(["a@chatroom"], memory_enabled=True, memory="旧记忆")
        sched, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        self.assertEqual(len(summ.memory_calls), 1)
        # 模拟 WebUI 在此期间保存了配置（换成完全不同的对象）
        sched._config = config_mod.load_assistant_config()
        disk = config_mod.load_assistant_config().digest_groups[0]
        self.assertEqual(disk.memory, "浓缩后的新记忆")
        self.assertEqual(disk.memory_rev, 1)

    def test_memory_prompt_uses_fresh_disk_value(self):
        """提交线程池之后磁盘记忆被改过 → prompt 里必须是新值。"""
        g = make_group(["a@chatroom"], memory_enabled=True, memory="旧记忆")
        sched, summ, _, _, _ = self.build(g, THREE_CHATS)
        config_mod.update_digest_group_memory("dg_001", "后台刚写的新记忆")
        sched._generate_digest(g, task_id=TASK_ID)
        self.assertIn("后台刚写的新记忆", summ.digest_calls[0]["user"])
        self.assertNotIn("旧记忆", summ.digest_calls[0]["user"])
        self.assertIn("后台刚写的新记忆", summ.memory_calls[0])

    def test_memory_skipped_when_disabled(self):
        g = make_group(["a@chatroom"], memory_enabled=False)
        _, summ, _, _, _ = self.run_digest(g, THREE_CHATS)
        self.assertEqual(summ.memory_calls, [])

    def test_memory_failure_does_not_break_digest(self):
        g = make_group(["a@chatroom"], memory_enabled=True)
        sched, summ, _, outbox, tc = self.build(g, THREE_CHATS)

        def boom(system_prompt, messages):
            raise RuntimeError("记忆接口挂了")

        summ._call_chat_api = boom
        sched._generate_digest(g, task_id=TASK_ID)
        tc.complete_task.assert_called_once()
        self.assertIn("默认摘要正文", outbox.last_content["digest"])


class TestPushThreeState(PipelineBase):
    """推送状态必须复用 delivery.aggregate_status 的三态，不得在业务侧重写。"""

    def _run_with_push(self, status):
        g = make_group(["a@chatroom", "b@chatroom"])
        sched, summ, store, outbox, tc = self.build(g, THREE_CHATS)
        delivery = MagicMock()
        delivery.send_text.return_value = {
            "success": status == "success", "status": status,
            "error": "" if status == "success" else "1 个渠道失败",
            "channels": [{"platform": "ilink", "ok": True}],
        }
        with patch("src.im.targets.bound_push_targets", return_value=["ilink", "qqbot"]), \
             patch("src.im.delivery.get_delivery_service", return_value=delivery), \
             patch("src.im.delivery.DeliveryService.outbox_channel", return_value="ilink,qqbot"):
            sched._generate_digest(g, task_id=TASK_ID)
        return delivery, outbox, tc

    def test_partial_is_recorded_as_partial(self):
        _, outbox, tc = self._run_with_push("partial")
        self.assertEqual(outbox.push_results[-1]["status"], "partial")
        tc.update_push_result.assert_called_once_with(TASK_ID, "partial", "1 个渠道失败")

    def test_success_recorded(self):
        _, outbox, tc = self._run_with_push("success")
        self.assertEqual(outbox.push_results[-1]["status"], "success")

    def test_failed_recorded(self):
        _, outbox, tc = self._run_with_push("failed")
        self.assertEqual(outbox.push_results[-1]["status"], "failed")

    def test_delivery_request_carries_group_id_and_outbox_id(self):
        delivery, outbox, _ = self._run_with_push("success")
        req = delivery.send_text.call_args.args[0]
        self.assertEqual(req.conversation_key, "dg_001")
        self.assertEqual(req.source_type, "group_digest")
        self.assertEqual(req.outbox_id, 101)
        self.assertEqual(req.source_id, "101")
        self.assertTrue(req.auto_route)

    def test_no_bound_channel_is_skipped(self):
        g = make_group(["a@chatroom"])
        _, _, _, outbox, tc = self.run_digest(g, THREE_CHATS)
        self.assertEqual(outbox.push_results[-1]["status"], "skipped")


class TestTaskResult(PipelineBase):

    def test_result_holds_full_digest_for_repush(self):
        """重推的三级兜底会读 task.result，必须是完整正文不截断。"""
        long_text = "## 话题\n" + "很长的摘要内容" * 300
        g = make_group(["a@chatroom", "b@chatroom"])
        summ = FakeSummarizer(default=long_text)
        _, _, _, _, tc = self.run_digest(g, THREE_CHATS, summarizer=summ)
        kwargs = tc.complete_task.call_args.kwargs
        self.assertEqual(kwargs["result"], long_text)
        self.assertEqual(kwargs["msg_count"], 3)

    def test_progress_reports_degradation(self):
        g = make_group(["a@chatroom", "b@chatroom", "c@chatroom"])
        sched, _, _, _, tc = self.build(g, big_chats(("a", "b", "c")))
        with patch.object(sched_mod, "digest_token_budget", return_value=1):
            sched._generate_digest(g, task_id=TASK_ID)
        progresses = [c.kwargs.get("progress", "") for c in tc.update_task.call_args_list]
        self.assertTrue(any("逐会话摘要" in p for p in progresses), progresses)
        self.assertTrue(any("3/3" in p for p in progresses), progresses)


if __name__ == "__main__":
    unittest.main()
