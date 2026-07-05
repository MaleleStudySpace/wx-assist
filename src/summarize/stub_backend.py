"""Stub summarizer — used when AI is not configured.

Returns polite "not configured" messages instead of crashing.
All non-AI features (收藏, 朋友圈, 聊天导出, 关键词提醒) work normally.
"""
import logging
import time

from .base import AbstractSummarizer
from .models import SummaryResult

logger = logging.getLogger(__name__)


class StubSummarizer(AbstractSummarizer):
    """No-op summarizer for when AI_PROVIDER_API_KEY is not set."""

    _backend_name: str = "stub"
    model: str = "none"

    def summarize(self, messages, requester_name="", **kwargs):
        return SummaryResult(
            summary_text="[AI 未配置]",
            topics=[], participants=[], decisions=[], action_items=[],
        )

    def _summarize_direct(self, messages, requester_name=""):
        return SummaryResult(
            summary_text="[AI 未配置]",
            topics=[], participants=[], decisions=[], action_items=[],
        )

    def _summarize_chunk(self, chunk, chunk_num, total, requester_name=""):
        return "[AI 未配置]"

    def _merge_chunk_summaries(self, chunk_summaries, requester_name=""):
        return SummaryResult(
            summary_text="[AI 未配置]",
            topics=[], participants=[], decisions=[], action_items=[],
        )

    def consolidate_memory(self, existing_memory, new_messages):
        return existing_memory or ""

    def _call_chat_api(self, system_prompt, messages):
        raise RuntimeError("AI 未配置，无法调用聊天接口")

    def _call_digest_api(self, system_prompt, messages):
        raise RuntimeError("AI 未配置，无法调用摘要接口")

    def _call_long_api(self, system_prompt, messages, **kwargs):
        raise RuntimeError("AI 未配置，无法调用长文本接口")

    def _call_chat_api_stream(self, system_prompt, messages):
        """Yield a single message indicating AI is not configured."""
        yield "AI 未配置，请先在系统配置中设置 AI 提供商。"

    def chat(self, message="", **kwargs):
        return "AI 未配置，请先在系统配置中设置 AI 提供商。"

    def agent_chat(self, system_prompt, messages, tools):
        raise NotImplementedError("AI 未配置，无法使用 Agent 功能。")

    def custom_prompt(self, messages, prompt, **kwargs):
        return "AI 未配置，请先在系统配置中设置 AI 提供商。"
