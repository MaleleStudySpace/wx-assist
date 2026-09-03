"""Claude (Anthropic) summarization backend.

Uses the Anthropic Python SDK with native structured output (Pydantic parse).
"""

import json
import logging
import time
from typing import Iterator

import anthropic

from .base import AbstractSummarizer
from .errors import LLMResponseError
from .prompts import MEMORY_CONSOLE_PROMPT
from ..utils.llm_logger import log_llm_interaction

logger = logging.getLogger(__name__)


class ClaudeSummarizer(AbstractSummarizer):
    """Summarization via Anthropic Claude API.

    Features:
    - Native structured output via client.messages.parse() + Pydantic
    - Token budget: 150K (safe margin below 200K context window)
    - Map-Reduce chunking for large conversations
    """

    # Claude-specific constants
    MODEL_HAIKU = "claude-haiku-4-5-20251001"
    MODEL_SONNET = "claude-sonnet-4-5-20250929"

    # 200K context window → 150K safe budget
    token_budget = 150_000

    _backend_name = "claude"

    retry_exceptions = (
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
    )

    def __init__(self, api_key: str,
                 model: str = MODEL_HAIKU,
                 base_url: str = "https://api.anthropic.com",
                 chunk_size: int = 400,
                 max_retries: int = 3):
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self.model = model
        self.chunk_size = chunk_size
        self.max_retries = max_retries

    # ── Conversational chat API call (called by base class) ─────

    def _extract_text(self, response, *, log_tag: str) -> str:
        """Safely pull text out of an Anthropic response.

        ``response.content`` can be an empty list, which made ``content[0]``
        raise a bare IndexError carrying no diagnostic context.
        """
        blocks = getattr(response, "content", None)
        if not blocks:
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
            raise LLMResponseError(
                f"[{log_tag}] LLM 返回空 content，model={self.model}，"
                f"input_tokens={input_tokens if input_tokens is not None else '未知'}",
                prompt_tokens=input_tokens, raw=response,
            )
        return blocks[0].text or ""

    def _call_chat_api(self, system_prompt: str,
                        messages: list[dict]) -> str:
        """Claude-specific: uses client.messages.create() with system param."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=system_prompt,
            messages=messages,
        )
        return self._extract_text(response, log_tag="CHAT-API") or "..."

    def _call_digest_api(self, system_prompt: str,
                         messages: list[dict],
                         timeout: float | None = None) -> str:
        """Digest-specific: higher max_tokens than chat for custom_prompt path."""
        kwargs = {}
        if timeout:
            kwargs["timeout"] = self._request_timeout(timeout)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            **kwargs,
        )
        return self._extract_text(response, log_tag="DIGEST-API") or "..."

    def _call_long_api(self, system_prompt: str,
                       messages: list[dict],
                       max_tokens: int = 2000,
                       temperature: float = 0.3,
                       timeout: float | None = None) -> str:
        """Long-form API call with configurable params for OA digest etc."""
        kwargs = {}
        if timeout:
            kwargs["timeout"] = self._request_timeout(timeout)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=messages,
            **kwargs,
        )
        return self._extract_text(response, log_tag="LONG-API") or "..."

    def _call_chat_api_stream(self, system_prompt: str,
                               messages: list[dict],
                               max_tokens: int = 2000) -> Iterator[str]:
        """Stream chat API response, yielding token strings."""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    # ── Agent chat (tool calling) ──────────────────────────────────

    def agent_chat(self, system_prompt: str,
                   messages: list[dict],
                   tools: list[dict]) -> tuple[str, list[dict] | None]:
        """Agent chat with tool calling via Anthropic API."""
        def _to_anthropic_tool(t: dict) -> dict:
            fn = t["function"]
            return {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn["parameters"],
            }

        request_kwargs = dict(
            model=self.model,
            max_tokens=2000,
            system=system_prompt,
            messages=messages,
        )
        if tools:
            request_kwargs["tools"] = [_to_anthropic_tool(t) for t in tools]

        start = time.monotonic()
        try:
            response = self._retry_with_backoff(
                lambda: self.client.messages.create(**request_kwargs),
                "agent chat",
            )
            latency = (time.monotonic() - start) * 1000
        except Exception:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] agent_chat FAILED after %.1fms", latency)
            raise

        content_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                })

        content = "".join(content_parts) if content_parts else None

        # If LLM called tools but left content empty, annotate so log is meaningful
        log_content = content or ""
        if not log_content and tool_calls:
            tool_names_str = ", ".join(tc["function"]["name"] for tc in tool_calls)
            log_content = f"[调用工具: {tool_names_str}]"

        # Build user_prompt string from messages (for logging)
        user_lines = []
        for m in messages:
            role = m.get("role", "unknown")
            text = m.get("content", "")
            user_lines.append(f"[{role}]: {text}")
        user_prompt = "\n".join(user_lines)

        # Tool descriptions for logging
        tool_names = [t.get("function", {}).get("name", "?") for t in tools]

        try:
            usage = response.usage
            token_in = usage.input_tokens if usage else 0
            token_out = usage.output_tokens if usage else 0
        except Exception:
            token_in = token_out = 0

        log_llm_interaction(
            backend="claude", call_type="agent_chat",
            model=self.model, system_prompt=system_prompt,
            user_prompt=user_prompt, response=log_content,
            latency_ms=latency,
            token_in=token_in, token_out=token_out,
            extra={"tool_calls": len(tool_calls) if tool_calls else 0,
                   "tools": ",".join(tool_names),
                   "tool_defs": tools,
                   "messages": len(messages)},
        )

        return content or "", tool_calls or None, ""

    # ── Memory consolidation (Claude backend) ───────────────────────

    def consolidate_memory(self, existing_memory: str,
                           new_messages: list[dict]) -> str:
        """Update group memory by incorporating new messages.

        Uses Claude Haiku for low cost and latency.  Returns the updated
        first-person diary-style memory text (≤2000 chars).

        Args:
            existing_memory: Current memory text (empty string if first time).
            new_messages: List of new message dicts to incorporate.

        Returns:
            Updated memory text, or existing_memory unchanged on failure.
        """
        if not new_messages:
            return existing_memory

        # Format new messages for the prompt
        msg_lines = []
        for m in new_messages[-200:]:  # cap at 200 messages per consolidation
            sender = m.get("sender_name", "?")
            content = m.get("content", "")
            if content:
                msg_lines.append(f"{sender}: {content}")

        if not msg_lines:
            return existing_memory

        existing_display = (
            existing_memory if existing_memory
            else "（暂无，这是第一次整理记忆）"
        )

        system_prompt = MEMORY_CONSOLE_PROMPT.format(
            existing_memory=existing_display,
            new_messages="\n".join(msg_lines),
        )

        def call():
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": "请输出更新后的完整记忆日记。",
                }],
            )
            text = response.content[0].text or ""
            # Enforce 2000-char soft cap
            if len(text) > 2000:
                text = text[:2000]
            return text.strip()

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, "memory consolidation")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="claude", call_type="consolidate_memory",
                model=self.model, system_prompt=system_prompt,
                user_prompt="请输出更新后的完整记忆日记。",
                response=result, latency_ms=latency,
                extra={"msg_count": len(msg_lines), "existing_len": len(existing_memory)},
            )
            return result
        except RuntimeError as e:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] consolidate_memory FAILED after %.1fms", latency)
            logger.warning("Memory consolidation failed: %s", e)
            return existing_memory  # don't lose existing memory on failure
