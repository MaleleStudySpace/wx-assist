"""摘要路径的 per-request 超时。

实测：延迟由**输出长度**决定而非输入长度 —— 固定开销约 18s，之后约 14ms/字。
6 个会话打包、输出 1542 字时耗时 40.6s，已经逼近 client 级的 60s httpx 超时。
所以摘要路径要单独放宽，但**不能动全局 client**（AI 对话路径依赖 60s 快速失败），
也不能让 connect 超时跟着变长（否则"连不上"要等满整个超时才失败）。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.summarize.base import AbstractSummarizer
from src.summarize.deepseek_backend import OpenAICompatSummarizer
from src.summarize.stub_backend import StubSummarizer


def ok_response(text="摘要正文"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=text, reasoning_content=None))])


def thinking_exhausted():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None, reasoning_content="让我分析一下"))])


MSGS = [{"role": "user", "content": "x"}]


class TestRequestTimeoutHelper(unittest.TestCase):

    def test_widens_read_write_pool_but_keeps_connect_short(self):
        t = AbstractSummarizer._request_timeout(180)
        self.assertEqual(t.read, 180.0)
        self.assertEqual(t.write, 180.0)
        self.assertEqual(t.pool, 180.0)
        self.assertEqual(t.connect, 10.0)

    def test_accepts_int_and_str_float(self):
        self.assertEqual(AbstractSummarizer._request_timeout(90).read, 90.0)


class TestDigestTimeout(unittest.TestCase):

    def setUp(self):
        self.s = OpenAICompatSummarizer(
            api_key="sk-test-not-real", model="test-model",
            base_url="https://example.invalid/v1")
        patcher = patch.object(self.s.client.chat.completions, "create")
        self.create = patcher.start()
        self.addCleanup(patcher.stop)
        self.create.return_value = ok_response()

    def test_digest_api_passes_timeout_on_main_call(self):
        self.s._call_digest_api("sys", MSGS, timeout=180)
        kwargs = self.create.call_args.kwargs
        self.assertIn("timeout", kwargs)
        self.assertEqual(kwargs["timeout"].read, 180.0)
        self.assertEqual(kwargs["timeout"].connect, 10.0)

    def test_timeout_applies_to_all_three_thinking_guard_channels(self):
        """主通道 + thinking-disabled 通道 + max_tokens 加倍通道都要带上。"""
        self.create.side_effect = [
            thinking_exhausted(),
            ValueError("400 unsupported field: thinking"),
            ok_response("降级后的正文"),
        ]
        self.s._call_digest_api("sys", MSGS, timeout=180)
        self.assertEqual(self.create.call_count, 3)
        for call in self.create.call_args_list:
            self.assertIn("timeout", call.kwargs)
            self.assertEqual(call.kwargs["timeout"].read, 180.0)
            self.assertEqual(call.kwargs["timeout"].connect, 10.0)

    def test_long_api_accepts_timeout(self):
        self.s._call_long_api("sys", MSGS, timeout=120)
        self.assertEqual(self.create.call_args.kwargs["timeout"].read, 120.0)

    def test_chat_api_does_not_pass_timeout(self):
        """对话与记忆更新路径必须完全不受影响。"""
        self.s._call_chat_api("sys", MSGS)
        self.assertNotIn("timeout", self.create.call_args.kwargs)

    def test_timeout_none_adds_no_kwarg(self):
        """向后兼容：不传 timeout 时不能凭空多出一个键。"""
        self.s._call_digest_api("sys", MSGS)
        self.assertNotIn("timeout", self.create.call_args.kwargs)
        self.s._call_long_api("sys", MSGS)
        self.assertNotIn("timeout", self.create.call_args.kwargs)

    def test_client_level_timeout_unchanged(self):
        """全局 httpx client 仍是 60s/10s —— per-request 覆盖不改动它。"""
        t = self.s.client._client.timeout
        self.assertEqual(t.read, 60.0)
        self.assertEqual(t.connect, 10.0)


class TestOtherBackendsAcceptTimeout(unittest.TestCase):

    def test_stub_backend_accepts_timeout_kwarg(self):
        with self.assertRaises(RuntimeError):
            StubSummarizer()._call_digest_api("sys", MSGS, timeout=180)

    def test_claude_backend_passes_timeout(self):
        from src.summarize.claude_backend import ClaudeSummarizer
        s = ClaudeSummarizer(api_key="sk-ant-test", model="claude-test",
                             base_url="https://example.invalid")
        with patch.object(s.client.messages, "create") as create:
            create.return_value = SimpleNamespace(
                content=[SimpleNamespace(text="正文")])
            self.assertEqual(s._call_digest_api("sys", MSGS, timeout=180), "正文")
            self.assertEqual(create.call_args.kwargs["timeout"].read, 180.0)
            self.assertEqual(create.call_args.kwargs["timeout"].connect, 10.0)

    def test_claude_backend_no_timeout_adds_no_kwarg(self):
        from src.summarize.claude_backend import ClaudeSummarizer
        s = ClaudeSummarizer(api_key="sk-ant-test", model="claude-test",
                             base_url="https://example.invalid")
        with patch.object(s.client.messages, "create") as create:
            create.return_value = SimpleNamespace(
                content=[SimpleNamespace(text="正文")])
            s._call_digest_api("sys", MSGS)
            self.assertNotIn("timeout", create.call_args.kwargs)

    def test_claude_chat_api_signature_unchanged(self):
        """对话路径签名不能变，否则既有调用方会炸。"""
        from src.summarize.claude_backend import ClaudeSummarizer
        import inspect
        params = list(inspect.signature(ClaudeSummarizer._call_chat_api).parameters)
        self.assertEqual(params, ["self", "system_prompt", "messages"])


class TestSchedulerPassesDigestTimeout(unittest.TestCase):
    """群摘要的 LLM 调用必须真的带上超时，否则常量是死的。"""

    def setUp(self):
        import os
        import tempfile
        from src.assistant.config import AssistantConfig, DigestGroup
        from src.assistant import scheduler as sched_mod
        self.sched_mod = sched_mod
        self.DigestGroup = DigestGroup
        self.AssistantConfig = AssistantConfig

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # 否则会读写真实的 data/scheduler_state.json
        patch.object(sched_mod, "_STATE_PATH",
                     os.path.join(tmp.name, "state.json")).start()
        # 否则会写真实的 llm 日志库和生产 assistant_config.json
        patch.object(sched_mod, "log_llm_interaction", MagicMock()).start()
        patch.object(sched_mod, "save_assistant_config", MagicMock()).start()
        # 空绑定 → 走 skipped 分支，不触碰真实推送渠道
        patch("src.im.targets.bound_push_targets", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_generate_digest_passes_timeout_to_llm(self):
        import time
        dg = self.DigestGroup(chat_id="1@chatroom", group_name="测试群",
                              lookback_hours=6, memory_enabled=False)
        cfg = self.AssistantConfig(assistant_enabled=True, digest_groups=[dg])

        summarizer = MagicMock()
        summarizer._backend_name = "fake"
        summarizer.model = "fake-model"
        summarizer._call_digest_api.return_value = "## 话题\n下周排期已确认"

        store = MagicMock()
        store.get_messages_since.return_value = [
            {"content": "今天讨论了下周排期", "sender_name": "A",
             "timestamp": int(time.time()), "msg_type": 1},
        ]
        outbox = MagicMock()
        outbox.add.return_value = 7

        sched = self.sched_mod.DigestScheduler(
            cfg, outbox, summarizer, store, task_center=MagicMock())
        sched._generate_digest(dg, task_id=None)

        kwargs = summarizer._call_digest_api.call_args.kwargs
        self.assertEqual(kwargs.get("timeout"),
                         self.sched_mod.DIGEST_LLM_TIMEOUT_SEC)
        self.assertEqual(kwargs.get("timeout"), 180.0)

    def test_digest_timeout_default_is_180(self):
        self.assertEqual(self.sched_mod.DIGEST_LLM_TIMEOUT_SEC, 180.0)


if __name__ == "__main__":
    unittest.main()
