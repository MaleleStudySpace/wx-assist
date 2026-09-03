"""LLM 异常体系：provider 返回不可用响应体时必须抛可诊断的错误。

修复前的真实故障：中转 API 在上下文超限时返回 HTTP **200** +
``{"choices": null, "base_resp": {"status_code": 2013, "status_msg":
"invalid params, context window exceeds limit"}}``。因为状态码是 200，
OpenAI SDK 不抛任何 HTTP 错误，``response.choices[0]`` 直接炸成
``TypeError: 'NoneType' object is not subscriptable``，
scheduler 把它拼成"摘要生成失败: 'NoneType' object is not subscriptable"
推进任务中心 —— 用户完全看不出是上下文超限。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.summarize.errors import (
    LLMContextOverflowError,
    LLMError,
    LLMResponseError,
)
from src.summarize.deepseek_backend import OpenAICompatSummarizer

# 实测值：6 群 24h 未清洗打包 = 703,597 字符 → 299,247 prompt_tokens → 被拒
REAL_OVERFLOW_TOKENS = 299247
OVERFLOW_MSG = "invalid params, context window exceeds limit"


def overflow_response(prompt_tokens=REAL_OVERFLOW_TOKENS, status_code=2013,
                      status_msg=OVERFLOW_MSG):
    return SimpleNamespace(
        choices=None,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens),
        base_resp={"status_code": status_code, "status_msg": status_msg},
    )


def thinking_exhausted():
    """主调用成功但 content 为空、reasoning 有内容 → 触发双通道降级。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None, reasoning_content="让我分析一下这些群聊内容"))])


def ok_response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=text, reasoning_content=None))])


class PydanticLikeResponse:
    """base_resp 藏在 __pydantic_extra__ 里的情况（SDK 的 extra 字段机制）。"""

    def __init__(self, extra):
        self.choices = None
        self.usage = SimpleNamespace(prompt_tokens=REAL_OVERFLOW_TOKENS)
        self.__pydantic_extra__ = extra


class TestErrorHierarchy(unittest.TestCase):

    def test_llm_error_is_runtime_error(self):
        """继承 RuntimeError 以兼容既有的 except RuntimeError 兜底。"""
        self.assertTrue(issubclass(LLMError, RuntimeError))

    def test_subclass_chain(self):
        self.assertTrue(issubclass(LLMResponseError, LLMError))
        self.assertTrue(issubclass(LLMContextOverflowError, LLMResponseError))

    def test_overflow_is_not_retried_by_backoff(self):
        """重试同样的输入只会同样失败，超限不能进 backoff 重试。"""
        self.assertFalse(issubclass(LLMError, OpenAICompatSummarizer.retry_exceptions))


class TestExtractChoice(unittest.TestCase):

    def setUp(self):
        self.s = OpenAICompatSummarizer(
            api_key="sk-test-not-real", model="test-model",
            base_url="https://example.invalid/v1")

    def test_normal_response_passes_through(self):
        choice = SimpleNamespace(message=SimpleNamespace(content="ok"))
        r = SimpleNamespace(choices=[choice])
        self.assertIs(self.s._extract_choice(r, log_tag="T"), choice)

    def test_null_choices_with_2013_raises_overflow(self):
        r = overflow_response()
        with self.assertRaises(LLMContextOverflowError) as cm:
            self.s._extract_choice(r, log_tag="DIGEST-API")
        e = cm.exception
        self.assertEqual(e.prompt_tokens, REAL_OVERFLOW_TOKENS)
        self.assertEqual(e.status_code, 2013)
        self.assertEqual(e.status_msg, OVERFLOW_MSG)
        self.assertIs(e.raw, r)
        # 错误串必须自带诊断信息，因为它会一路走到任务中心和推送正文
        self.assertIn(str(REAL_OVERFLOW_TOKENS), str(e))
        self.assertIn("2013", str(e))
        self.assertIn("DIGEST-API", str(e))

    def test_empty_choices_list_treated_same_as_null(self):
        r = overflow_response()
        r.choices = []
        with self.assertRaises(LLMContextOverflowError):
            self.s._extract_choice(r, log_tag="T")

    def test_overflow_detected_by_keyword_without_status_code(self):
        """不同中转的业务码不一样，关键词兜底。"""
        for msg in ("maximum context length exceeded",
                    "input is too long for requested model",
                    "context window limit reached"):
            with self.subTest(msg=msg):
                r = overflow_response(status_code=None, status_msg=msg)
                with self.assertRaises(LLMContextOverflowError):
                    self.s._extract_choice(r, log_tag="T")

    def test_base_resp_from_pydantic_extra(self):
        r = PydanticLikeResponse({"base_resp": {"status_code": 2013,
                                                "status_msg": OVERFLOW_MSG}})
        with self.assertRaises(LLMContextOverflowError) as cm:
            self.s._extract_choice(r, log_tag="T")
        self.assertEqual(cm.exception.prompt_tokens, REAL_OVERFLOW_TOKENS)

    def test_base_resp_as_object_not_dict(self):
        r = SimpleNamespace(
            choices=None,
            usage=SimpleNamespace(prompt_tokens=12345),
            base_resp=SimpleNamespace(status_code=2013, status_msg=OVERFLOW_MSG))
        with self.assertRaises(LLMContextOverflowError) as cm:
            self.s._extract_choice(r, log_tag="T")
        self.assertEqual(cm.exception.prompt_tokens, 12345)

    def test_null_choices_without_base_resp_raises_response_error(self):
        r = SimpleNamespace(choices=None, usage=None)
        with self.assertRaises(LLMResponseError) as cm:
            self.s._extract_choice(r, log_tag="T")
        self.assertNotIsInstance(cm.exception, LLMContextOverflowError)
        self.assertIsNone(cm.exception.prompt_tokens)
        self.assertIn("未知", str(cm.exception))

    def test_never_degrades_to_bare_typeerror(self):
        """回归护栏：修复前这里抛的是 TypeError。"""
        r = SimpleNamespace(choices=None, usage=None)
        try:
            self.s._extract_choice(r, log_tag="T")
        except TypeError as e:
            self.fail("又退化成不可诊断的 TypeError 了: %s" % e)
        except LLMResponseError:
            pass
        else:
            self.fail("choices 为 null 时必须抛异常")


class TestOverflowPropagation(unittest.TestCase):
    """超限必须冒泡到调用方，否则 C5 的自动降级拿不到信号。"""

    def setUp(self):
        self.s = OpenAICompatSummarizer(
            api_key="sk-test-not-real", model="test-model",
            base_url="https://example.invalid/v1")
        self.patcher = patch.object(self.s.client.chat.completions, "create")
        self.create = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_main_call_overflow_propagates_without_retry(self):
        """真实场景：provider 在第一次调用就返回 choices=null。"""
        self.create.return_value = overflow_response()
        with self.assertRaises(LLMContextOverflowError):
            self.s._call_digest_api("sys", [{"role": "user", "content": "x"}])
        self.assertEqual(self.create.call_count, 1)

    def test_channel1_overflow_short_circuits_and_propagates(self):
        """主调用 thinking 耗尽 → 通道 1 超限 → 直接冒泡，不再试通道 2。

        换通道不可能改善超限（输入相同、max_tokens 反而更大），所以只调 2 次。
        """
        self.create.side_effect = [thinking_exhausted(), overflow_response()]
        with self.assertRaises(LLMContextOverflowError):
            self.s._call_digest_api("sys", [{"role": "user", "content": "x"}])
        self.assertEqual(self.create.call_count, 2)

    def test_channel2_overflow_also_propagates(self):
        """通道 1 因不认识 thinking 字段失败 → 通道 2 超限 → 同样必须冒泡。"""
        self.create.side_effect = [
            thinking_exhausted(),
            ValueError("400 unsupported field: thinking"),
            overflow_response(),
        ]
        with self.assertRaises(LLMContextOverflowError):
            self.s._call_digest_api("sys", [{"role": "user", "content": "x"}])
        self.assertEqual(self.create.call_count, 3)

    def test_overflow_never_degrades_to_ellipsis(self):
        """回归护栏：吞掉超限会让 _call_digest_api 返回 "..."（真值），
        任务会被误判为成功、正文是一串点。"""
        self.create.side_effect = [thinking_exhausted(), overflow_response()]
        try:
            result = self.s._call_digest_api("sys", [{"role": "user", "content": "x"}])
        except LLMContextOverflowError:
            return
        self.fail("超限被吞掉了，返回了 %r" % (result,))

    def test_channel1_rejected_falls_through_to_channel2(self):
        """通道 1 因不认识 thinking 字段而 400 → 仍应降级到通道 2 成功。"""
        self.create.side_effect = [
            thinking_exhausted(),
            ValueError("400 unsupported field: thinking"),
            ok_response("降级后的正文"),
        ]
        self.assertEqual(
            self.s._call_digest_api("sys", [{"role": "user", "content": "x"}]),
            "降级后的正文")

    def test_successful_call_returns_content(self):
        self.create.return_value = ok_response("正常摘要")
        self.assertEqual(
            self.s._call_digest_api("sys", [{"role": "user", "content": "x"}]),
            "正常摘要")


class TestClaudeExtractText(unittest.TestCase):

    def setUp(self):
        from src.summarize.claude_backend import ClaudeSummarizer
        self.s = ClaudeSummarizer(
            api_key="sk-ant-test", model="claude-test",
            base_url="https://example.invalid")

    def test_normal_content_passes_through(self):
        r = SimpleNamespace(content=[SimpleNamespace(text="正文")])
        self.assertEqual(self.s._extract_text(r, log_tag="T"), "正文")

    def test_empty_content_raises_response_error_not_indexerror(self):
        r = SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=42))
        with self.assertRaises(LLMResponseError) as cm:
            self.s._extract_text(r, log_tag="DIGEST-API")
        self.assertEqual(cm.exception.prompt_tokens, 42)
        self.assertIn("42", str(cm.exception))

    def test_missing_content_attr_raises_response_error(self):
        with self.assertRaises(LLMResponseError):
            self.s._extract_text(SimpleNamespace(), log_tag="T")


if __name__ == "__main__":
    unittest.main()
