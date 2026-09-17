"""LLM 失败可观测性回归测试。

修复前的真实故障：公众号即时提醒里 LLM 调用失败，bot.log 只有一行
``[LLM] chat FAILED after 624.0ms``，``data/llm.log`` 里没有任何记录，也看不出
上游到底返回了什么（HTTP 状态码 / provider 业务码 / 异常类型全部丢失）。

本文件锁死三件事：
  1. ``AbstractSummarizer.chat()`` 失败时同样写 ``data/llm.log``，并带上
     backend / model / 请求 prompt / 异常类型 / HTTP 状态码 / provider 业务码；
  2. 失败日志里的凭证被脱敏（不把 API Key 写进日志）；
  3. 公众号即时提醒摘要失败会打 warning（含文章标题 + URL），且业务兜底行为
     不变 —— 仍回退到原始 digest、仍正常写 Outbox。
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.assistant.config import AssistantConfig, OAMonitorGroup
from src.db.content_cache import ContentCache
from src.summarize.base import AbstractSummarizer, _error_diagnostics
from src.summarize.errors import LLMContextOverflowError, LLMResponseError
from src.utils import llm_logger as llm_logger_mod


class _LogCapture(logging.Handler):
    """收走 llm_logger 模块 logger 的输出，避免依赖真实的 data/llm.log。"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record):  # noqa: D102
        try:
            self.messages.append(record.getMessage())
        except Exception:  # pragma: no cover - 防御格式化异常
            pass


class _TransientStatusError(Exception):
    """模拟 openai 的 APIStatusError：带 HTTP 状态码、可重试。"""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class _FailingSummarizer(AbstractSummarizer):
    _backend_name = "testbackend"
    model = "test-model"
    max_retries = 2
    retry_exceptions = ()

    def __init__(self, error):
        self.error = error

    def _call_chat_api(self, system_prompt, messages):
        raise self.error

    def _call_digest_api(self, system_prompt, messages, timeout=None):
        raise self.error

    def _call_long_api(self, system_prompt, messages, max_tokens=2000,
                       temperature=0.3, timeout=None):
        raise self.error

    def _call_chat_api_stream(self, system_prompt, messages, max_tokens=2000):
        raise self.error

    def agent_chat(self, system_prompt, messages, tools):
        raise self.error

    def consolidate_memory(self, existing_memory, new_messages):
        return existing_memory


class _RetryingSummarizer(_FailingSummarizer):
    """可重试异常 + 重试耗尽，验证 __cause__ 链被保留。"""

    retry_exceptions = (_TransientStatusError,)
    max_retries = 2


class TestChatFailureLogging(unittest.TestCase):

    def setUp(self):
        self.capture = _LogCapture()
        llm_logger_mod.logger.addHandler(self.capture)
        llm_logger_mod.logger.setLevel(logging.DEBUG)
        self.addCleanup(llm_logger_mod.logger.removeHandler, self.capture)
        # 隔离真实 data/llm.log 文件
        patcher = patch.object(llm_logger_mod, "_get_llm_logger",
                               return_value=MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _detail_record(self) -> dict:
        for msg in self.capture.messages:
            if msg.startswith("[LLM-DETAIL] "):
                return json.loads(msg[len("[LLM-DETAIL] "):])
        self.fail(f"没有写入 [LLM-DETAIL] 失败记录：{self.capture.messages}")

    def _summary_text(self) -> str:
        return "\n".join(self.capture.messages)

    # ── 1. 失败也写交互日志 ────────────────────────────────────────────

    def test_failure_writes_interaction_with_status_and_metadata(self):
        err = LLMContextOverflowError(
            "[DIGEST-API] LLM 返回空 choices（base_resp=2013: "
            "invalid params, context window exceeds limit），prompt_tokens=299247",
            prompt_tokens=299247, status_code=2013,
            status_msg="invalid params, context window exceeds limit",
        )
        smrz = _FailingSummarizer(err)

        with self.assertRaises(LLMContextOverflowError):
            smrz.chat("请总结这篇文章", requester_name="system",
                      group_name="测试公众号")

        detail = self._detail_record()
        self.assertEqual(detail["backend"], "testbackend")
        self.assertEqual(detail["model"], "test-model")
        self.assertEqual(detail["call_type"], "chat")
        self.assertTrue(detail["response"].startswith("[Error:"))
        self.assertIn("LLMContextOverflowError", detail["response"])

        extra = detail["extra"]
        self.assertEqual(extra["error_type"], "LLMContextOverflowError")
        self.assertEqual(extra["status_code"], 2013)
        self.assertEqual(extra["status_msg"],
                         "invalid params, context window exceeds limit")
        self.assertEqual(extra["prompt_tokens"], 299247)
        self.assertIn("测试公众号", extra["group"])

        summary = self._summary_text()
        self.assertIn("[LLM] chat | testbackend/test-model", summary)
        self.assertIn("FAILED", summary)

    def test_retry_exhaustion_preserves_status_code(self):
        """重试耗尽后 HTTP 状态码仍要能从异常链里提取。"""
        smrz = _RetryingSummarizer(_TransientStatusError(429, "rate limited"))
        with patch("src.summarize.base.time.sleep", lambda _s: None):
            with self.assertRaises(RuntimeError):
                smrz.chat("hi")

        extra = self._detail_record()["extra"]
        self.assertEqual(extra["status_code"], 429)
        self.assertEqual(extra["cause_type"], "_TransientStatusError")

    def test_original_exception_propagates_unchanged(self):
        """失败日志不能改变业务行为：原异常必须原样抛出。"""
        err = LLMResponseError("choices 为 null", status_code=2013)
        smrz = _FailingSummarizer(err)
        with self.assertRaises(LLMResponseError) as cm:
            smrz.chat("hi")
        self.assertIs(cm.exception, err)

    def test_logging_failure_does_not_mask_llm_error(self):
        """data/ 不可写等日志故障不能顶掉真正的 LLM 异常。

        否则调用方的 ``except RuntimeError`` 会失效，拿到一个与业务无关的
        OSError —— 这正是"新增日志反而破坏现有功能"的典型路径。
        """
        err = LLMResponseError("choices 为 null", status_code=2013)
        smrz = _FailingSummarizer(err)
        with patch("src.summarize.base.log_llm_interaction",
                   side_effect=OSError("data/ is read-only")):
            with self.assertRaises(LLMResponseError) as cm:
                smrz.chat("hi")
        self.assertIs(cm.exception, err)

    # ── 2. 凭证脱敏 ────────────────────────────────────────────────────

    def test_api_key_in_error_message_is_masked(self):
        err = LLMResponseError(
            "upstream rejected sk-super-secret-key for Authorization: Bearer tok-abc",
            status_code=401)
        smrz = _FailingSummarizer(err)
        with self.assertRaises(LLMResponseError):
            smrz.chat("hi")

        blob = json.dumps(self._detail_record(), ensure_ascii=False)
        self.assertNotIn("sk-super-secret-key", blob)
        self.assertNotIn("tok-abc", blob)
        self.assertIn("sk-***", blob)

    def test_success_path_still_logs_once(self):
        """成功路径不受影响：仍只写一条 OK 交互记录。"""

        class _OkSummarizer(_FailingSummarizer):
            def _call_chat_api(self, system_prompt, messages):
                return "正常回复"

        _OkSummarizer(LLMResponseError("unused")).chat("hi")
        detail = self._detail_record()
        self.assertEqual(detail["response"], "正常回复")
        self.assertIn("OK", self._summary_text())


class TestErrorDiagnostics(unittest.TestCase):

    def test_openai_status_error_shape(self):
        class _Response:
            status_code = 503

        class _ApiError(Exception):
            status_code = 503
            response = _Response()

        info = _error_diagnostics(_ApiError("service unavailable"))
        self.assertEqual(info["error_type"], "_ApiError")
        self.assertEqual(info["status_code"], 503)

    def test_response_only_status_code(self):
        class _Response:
            status_code = 500

        class _BareError(Exception):
            response = _Response()

        self.assertEqual(_error_diagnostics(_BareError("boom"))["status_code"], 500)

    def test_connection_error_has_no_status_code(self):
        info = _error_diagnostics(ConnectionError("Connection refused"))
        self.assertNotIn("status_code", info)
        self.assertEqual(info["error_type"], "ConnectionError")

    def test_self_referential_chain_does_not_loop(self):
        err = RuntimeError("loop")
        err.__cause__ = err
        self.assertEqual(_error_diagnostics(err)["error_type"], "RuntimeError")


class TestOAMonitorSummaryFailure(unittest.TestCase):
    """公众号即时提醒：摘要失败要可见，但兜底与推送行为保持不变。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache = ContentCache(str(Path(self.tmpdir.name) / "messages.db"))
        self.addCleanup(self.cache.stop_oa_content_fetcher)
        self.addCleanup(self.tmpdir.cleanup)
        self.cache.upsert("oa_accounts", {
            "gh_id": "gh_test", "display_name": "测试公众号",
            "avatar_url": "", "last_updated": 1,
        })

    def _engine(self):
        from src.assistant.oa_monitor import OAMonitorEngine

        outbox = MagicMock()
        outbox.query_by_url.return_value = False
        outbox.get_by_url.return_value = None
        outbox.add.return_value = 1
        config = AssistantConfig(
            assistant_enabled=True,
            oa_monitor_groups=[OAMonitorGroup(
                id="oam_test", name="测试监控",
                accounts=["gh_test"], enabled=True,
            )],
        )
        engine = OAMonitorEngine(config, outbox, content_cache=self.cache)
        return engine, outbox, config

    def _queued_job(self, url="https://example.test/llm-fail",
                    digest="微信原始摘要", full_content=None):
        article = {
            "url": url, "gh_id": "gh_test", "title": "让 Agent 直接调用 5000+ 数据源",
            "digest": digest, "source_name": "测试公众号",
        }
        if full_content is not None:
            self.cache.upsert("oa_cache", {
                "url": url, "gh_id": "gh_test", "title": article["title"],
                "digest": digest, "cover_url": "", "source_name": "测试公众号",
                "pub_time": 0, "full_content": full_content, "content_status": 1,
                "llm_summary": "", "llm_summary_ok": 0, "cached_at": 1,
            })
        self.cache._ensure_oa_job("instant_alert", article, group={
            "id": "oam_test", "name": "测试监控", "custom_prompt": "",
        })
        return article, self.cache.claim_oa_job("instant_alert")

    def test_llm_failure_is_warned_with_title_and_url(self):
        engine, outbox, config = self._engine()
        article, job = self._queued_job()
        group = config.oa_monitor_groups[0]
        summarizer = MagicMock()
        summarizer.chat.side_effect = LLMResponseError(
            "[CHAT-API] LLM 返回空 choices（base_resp=2013: "
            "invalid params, context window exceeds limit）",
            status_code=2013)

        with patch("src.assistant.oa_reader.fetch_article_content", return_value=""), \
             patch.object(engine, "_push_to_wechat", return_value=("skipped", "")), \
             patch("src.config.load_config", return_value=None), \
             patch("src.summarize.create_summarizer", return_value=summarizer):
            with self.assertLogs("src.assistant.oa_monitor", level="WARNING") as cm:
                engine._process_alert_job(job, group, config)

        text = "\n".join(cm.output)
        self.assertIn("LLM 调用失败", text)
        self.assertIn("让 Agent 直接调用", text)     # 文章标题
        self.assertIn(article["url"], text)            # 文章 URL
        self.assertIn("LLMResponseError", text)        # 异常类型

    def test_business_fallback_unchanged_on_llm_failure(self):
        """LLM 失败后仍用原始 digest 兜底，不出现「暂无文章摘要」，推送照常。"""
        engine, outbox, config = self._engine()
        article, job = self._queued_job()
        group = config.oa_monitor_groups[0]
        summarizer = MagicMock()
        summarizer.chat.side_effect = RuntimeError("upstream down")

        with patch("src.assistant.oa_reader.fetch_article_content", return_value=""), \
             patch.object(engine, "_push_to_wechat", return_value=("skipped", "")), \
             patch("src.config.load_config", return_value=None), \
             patch("src.summarize.create_summarizer", return_value=summarizer):
            with self.assertLogs("src.assistant.oa_monitor", level="WARNING"):
                engine._process_alert_job(job, group, config)

        self.assertEqual(summarizer.chat.call_count, 1)
        notification = outbox.add.call_args.args[3]
        self.assertIn("微信原始摘要", notification)
        self.assertNotIn("（暂无文章摘要）", notification)

    def test_no_digest_still_uses_placeholder(self):
        """有正文但 digest 为空且 LLM 失败时，兜底文案保持不变。"""
        engine, outbox, config = self._engine()
        article, job = self._queued_job(
            url="https://example.test/empty", digest="",
            full_content="这是一篇没有微信导语但已抓到正文的文章")
        group = config.oa_monitor_groups[0]
        summarizer = MagicMock()
        summarizer.chat.side_effect = RuntimeError("upstream down")

        with patch("src.assistant.oa_reader.fetch_article_content", return_value=""), \
             patch.object(engine, "_push_to_wechat", return_value=("skipped", "")), \
             patch("src.config.load_config", return_value=None), \
             patch("src.summarize.create_summarizer", return_value=summarizer):
            with self.assertLogs("src.assistant.oa_monitor", level="WARNING"):
                engine._process_alert_job(job, group, config)

        self.assertEqual(summarizer.chat.call_count, 1)
        notification = outbox.add.call_args.args[3]
        self.assertIn("（暂无文章摘要）", notification)

    def test_llm_success_path_unchanged(self):
        engine, outbox, config = self._engine()
        article, job = self._queued_job(url="https://example.test/ok")
        group = config.oa_monitor_groups[0]
        summarizer = MagicMock()
        summarizer.chat.return_value = "AI 生成的速读摘要"

        with patch("src.assistant.oa_reader.fetch_article_content", return_value=""), \
             patch.object(engine, "_push_to_wechat", return_value=("skipped", "")), \
             patch("src.config.load_config", return_value=None), \
             patch("src.summarize.create_summarizer", return_value=summarizer):
            engine._process_alert_job(job, group, config)

        notification = outbox.add.call_args.args[3]
        self.assertIn("AI 生成的速读摘要", notification)


if __name__ == "__main__":
    unittest.main()
