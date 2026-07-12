"""Claude (Anthropic) summarization backend.

Uses the Anthropic Python SDK with native structured output (Pydantic parse).
"""

import logging
import time
from typing import Iterator

import anthropic

from .base import AbstractSummarizer
from .models import SummaryResult
from .prompts import (
    SYSTEM_PROMPT,
    CHUNK_SYSTEM_PROMPT,
    MERGE_SYSTEM_PROMPT,
    MEMORY_CONSOLE_PROMPT,
    build_summary_prompt,
    build_chunk_summary_prompt,
    build_merge_prompt,
)
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

    def _call_chat_api(self, system_prompt: str,
                        messages: list[dict]) -> str:
        """Claude-specific: uses client.messages.create() with system param."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text or "..."

    def _call_digest_api(self, system_prompt: str,
                         messages: list[dict]) -> str:
        """Digest-specific: higher max_tokens than chat for custom_prompt path."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text or "..."

    def _call_long_api(self, system_prompt: str,
                       messages: list[dict],
                       max_tokens: int = 2000,
                       temperature: float = 0.3) -> str:
        """Long-form API call with configurable params for OA digest etc."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text or "..."

    def _call_chat_api_stream(self, system_prompt: str,
                               messages: list[dict]) -> Iterator[str]:
        """Stream chat API response, yielding token strings."""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=2000,
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
        response = self.client.messages.create(**request_kwargs)
        latency = (time.monotonic() - start) * 1000

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

        return content or "", tool_calls or None

    # ── Direct summarization ──────────────────────────────────────

    def _summarize_direct(self, messages: list[dict],
                           requester_name: str) -> SummaryResult:
        """All messages in one call — uses Pydantic parse for structured output."""
        user_prompt = build_summary_prompt(messages, requester_name)

        def call():
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=SummaryResult,
            )
            return response.parsed_output

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, "direct summarization")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="claude", call_type="summarize_direct",
                model=self.model, system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt, response=str(result),
                latency_ms=latency,
                extra={"requester": requester_name, "msg_count": len(messages)},
            )
            return result
        except RuntimeError:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] summarize_direct FAILED after %.1fms", latency)
            raise

    # ── Map-Reduce ────────────────────────────────────────────────

    def _summarize_chunk(self, chunk: list[dict], chunk_num: int,
                          total: int, requester_name: str) -> str:
        """Extract key facts from a single chunk (plain text output)."""
        user_prompt = build_chunk_summary_prompt(
            chunk, chunk_num, total, requester_name
        )

        def call():
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=CHUNK_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, f"chunk {chunk_num}/{total}")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="claude", call_type="summarize_chunk",
                model=self.model, system_prompt=CHUNK_SYSTEM_PROMPT,
                user_prompt=user_prompt, response=result,
                latency_ms=latency,
                extra={"chunk": f"{chunk_num}/{total}", "requester": requester_name},
            )
            return result
        except RuntimeError:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] summarize_chunk %d/%d FAILED after %.1fms",
                        chunk_num, total, latency)
            raise

    def _merge_chunk_summaries(self, chunk_summaries: list[str],
                                requester_name: str) -> SummaryResult:
        """Merge chunk summaries into final structured result."""
        user_prompt = build_merge_prompt(chunk_summaries, requester_name)

        def call():
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=8192,
                system=MERGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=SummaryResult,
            )
            return response.parsed_output

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, "merge chunk summaries")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="claude", call_type="merge_summaries",
                model=self.model, system_prompt=MERGE_SYSTEM_PROMPT,
                user_prompt=user_prompt, response=str(result),
                latency_ms=latency,
                extra={"chunk_count": len(chunk_summaries), "requester": requester_name},
            )
            return result
        except RuntimeError:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] merge_summaries FAILED after %.1fms", latency)
            raise

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
