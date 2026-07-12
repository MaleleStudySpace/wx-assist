"""DeepSeek summarization backend.

DeepSeek API is OpenAI-compatible. Uses the openai Python SDK with
tool calling for structured output.

Base URL: https://api.deepseek.com
Docs: https://platform.deepseek.com/api-docs
"""

import json
import logging
import time
from typing import Iterator

from openai import OpenAI, RateLimitError, APIConnectionError, APIStatusError

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

# DeepSeek API base URL
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Tool schema for structured output — matches SummaryResult Pydantic model
STORE_SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": "store_summary",
        "description": "Store a structured summary of a group chat conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "summary_text": {
                    "type": "string",
                    "description": "A 2-4 sentence overview of what was discussed",
                },
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Main topics discussed in the conversation",
                },
                "participants": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "contributions": {"type": "string"},
                        },
                        "required": ["name", "contributions"],
                        "additionalProperties": False,
                    },
                    "description": "Key participants and what they contributed",
                },
            },
            "required": ["summary_text", "topics", "participants"],
            "additionalProperties": False,
        },
    },
}


def _parse_summary_from_tool_call(response) -> SummaryResult:
    """Extract SummaryResult from DeepSeek response.

    Tries in order:
    1. Tool call → parse arguments JSON
    2. Content is valid JSON → parse as SummaryResult
    3. Plain text content → wrap in basic SummaryResult
    """
    choice = response.choices[0]
    msg = choice.message

    # Strategy 1: tool call with structured data
    if msg.tool_calls:
        args_json = msg.tool_calls[0].function.arguments
        data = json.loads(args_json)

        participants = []
        for p in data.get("participants", []):
            if isinstance(p, dict):
                participants.append(p)
            elif isinstance(p, str):
                participants.append({"name": p, "contributions": ""})

        return SummaryResult(
            summary_text=data.get("summary_text", ""),
            topics=data.get("topics", []),
            participants=participants,
        )

    # Strategy 2: JSON in message content
    content = msg.content or ""
    if isinstance(content, str) and content.strip():
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "summary_text" in data:
                return SummaryResult(**{
                    k: v for k, v in data.items()
                    if k in ("summary_text", "topics", "participants")
                })
        except (json.JSONDecodeError, Exception):
            pass

    # Strategy 3: plain text — wrap in minimal SummaryResult
    content = (content or "").strip()
    if content:
        logger.info("DeepSeek returned plain text (no tool call), wrapping as summary")
        return SummaryResult(
            summary_text=content[:2000],
            topics=[],
            participants=[],
        )

    raise RuntimeError("DeepSeek returned empty response")


class OpenAICompatSummarizer(AbstractSummarizer):
    """Summarization via OpenAI-compatible API (DeepSeek, OpenAI, local models, etc.).

    Uses tool calling for structured output since DeepSeek doesn't have
    native Pydantic parsing like Claude.

    Features:
    - OpenAI-compatible tool calling for structured output
    - Token budget: 100K (safe margin below 128K context window)
    - Map-Reduce chunking for large conversations
    """

    # DeepSeek model IDs
    MODEL_PRO = "deepseek-v4-pro"      # V4 Pro (flagship, 1M context)
    MODEL_FLASH = "deepseek-v4-flash"  # V4 Flash (fast/cheap, 1M context)

    # 1M context window → 900K safe budget
    token_budget = 900_000

    _backend_name = "deepseek"

    retry_exceptions = (RateLimitError, APIConnectionError, APIStatusError)

    def __init__(self, api_key: str,
                 model: str = MODEL_PRO,
                 base_url: str = DEEPSEEK_BASE_URL,
                 chunk_size: int = 400,
                 max_retries: int = 3,
                 extra_body: dict = None):
        # OpenAI SDK expects base_url with path prefix (e.g. /v1, /v2).
        # If the user provides a bare domain, append /v1 for backward compat.
        # If the user provides a URL with a path, keep it as-is.
        base_url = base_url.rstrip("/")
        import re
        path_part = base_url.split("://", 1)[-1]
        if not re.search(r'/\w', path_part):
            base_url += "/v1"

        # Some API proxies (Cloudflare) block the OpenAI SDK's default User-Agent.
        # Use a custom httpx client that overrides the UA to something neutral.
        import httpx
        def _fix_user_agent(request):
            request.headers["user-agent"] = "wx-assist/1.0"
        http_client = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=10.0),
            event_hooks={"request": [_fix_user_agent]},
        )
        self.client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        self.model = model
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self.extra_body = extra_body

    # ── Conversational chat API call (called by base class) ─────

    @staticmethod
    def _merge_params(defaults: dict, extra_body: dict | None) -> dict:
        """Merge extra_body into defaults: same key overrides, new keys appended."""
        merged = dict(defaults)
        if extra_body:
            merged.update(extra_body)
        return merged

    def _call_chat_api(self, system_prompt: str,
                        messages: list[dict]) -> str:
        """DeepSeek-specific: uses chat.completions.create() with system role."""
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = self._merge_params(
            {"model": self.model, "max_tokens": 400, "messages": api_messages},
            self.extra_body,
        )
        response = self.client.chat.completions.create(**params)
        content = response.choices[0].message.content
        if not content:
            logger.warning("[CHAT-API] LLM returned empty content (model=%s)", self.model)
            return "..."
        return content

    def _call_digest_api(self, system_prompt: str,
                         messages: list[dict]) -> str:
        """Digest-specific: higher max_tokens than chat for custom_prompt path."""
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = self._merge_params(
            {"model": self.model, "max_tokens": 4096, "messages": api_messages},
            self.extra_body,
        )
        response = self.client.chat.completions.create(**params)
        content = response.choices[0].message.content
        if not content:
            logger.warning("[DIGEST-API] LLM returned empty content (model=%s)", self.model)
            return "..."
        return content

    def _call_long_api(self, system_prompt: str,
                       messages: list[dict],
                       max_tokens: int = 2000,
                       temperature: float = 0.3) -> str:
        """Long-form API call with configurable params for OA digest etc."""
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = self._merge_params(
            {"model": self.model, "max_tokens": max_tokens,
             "temperature": temperature, "messages": api_messages},
            self.extra_body,
        )
        response = self.client.chat.completions.create(**params)
        content = response.choices[0].message.content
        if not content:
            logger.warning("[LONG-API] LLM returned empty content (model=%s)", self.model)
            return "..."
        return content

    def _call_chat_api_stream(self, system_prompt: str,
                               messages: list[dict]) -> Iterator[str]:
        """Stream chat API response, yielding token strings."""
        from typing import Iterator as _Iter
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = self._merge_params(
            {"model": self.model, "max_tokens": 2000,
             "messages": api_messages, "stream": True},
            self.extra_body,
        )
        stream = self.client.chat.completions.create(**params)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    # ── Agent chat (tool calling) ──────────────────────────────────

    def agent_chat(self, system_prompt: str,
                   messages: list[dict],
                   tools: list[dict]) -> tuple[str, list[dict] | None]:
        """Agent chat with tool calling via OpenAI-compatible API."""
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = self._merge_params(
            {"model": self.model, "max_tokens": 2000,
             "messages": api_messages,
             "tools": tools, "tool_choice": "auto"},
            self.extra_body,
        )

        start = time.monotonic()
        try:
            response = self.client.chat.completions.create(**params)
            latency = (time.monotonic() - start) * 1000
        except Exception:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] agent_chat FAILED after %.1fms", latency)
            raise

        msg = response.choices[0].message
        tool_calls = (
            [{
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            } for tc in msg.tool_calls]
            if msg.tool_calls else None
        )

        content = msg.content or ""

        # If LLM called tools but left content empty, annotate so log is meaningful
        if not content and tool_calls:
            tool_names_str = ", ".join(tc["function"]["name"] for tc in tool_calls)
            content = f"[调用工具: {tool_names_str}]"

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
            token_in = usage.prompt_tokens if usage else 0
            token_out = usage.completion_tokens if usage else 0
        except Exception:
            token_in = token_out = 0

        log_llm_interaction(
            backend="deepseek", call_type="agent_chat",
            model=self.model, system_prompt=system_prompt,
            user_prompt=user_prompt, response=content,
            latency_ms=latency,
            token_in=token_in, token_out=token_out,
            extra={"tool_calls": len(tool_calls) if tool_calls else 0,
                   "tools": ",".join(tool_names),
                   "tool_defs": tools,
                   "messages": len(messages)},
        )

        return content, tool_calls

    # ── Direct summarization ──────────────────────────────────────

    def _summarize_direct(self, messages: list[dict],
                           requester_name: str) -> SummaryResult:
        """All messages in one call — uses tool calling for structured output."""
        user_prompt = build_summary_prompt(messages, requester_name)

        def call():
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=8192,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[STORE_SUMMARY_TOOL],
                tool_choice="auto",  # V4 Flash doesn't support forced tool_choice with thinking
                extra_body=self.extra_body,
            )
            return _parse_summary_from_tool_call(response)

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, "direct summarization")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="deepseek", call_type="summarize_direct",
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
        """Extract key facts from a single chunk (plain text, no structured output)."""
        user_prompt = build_chunk_summary_prompt(
            chunk, chunk_num, total, requester_name
        )

        def call():
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": CHUNK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, f"chunk {chunk_num}/{total}")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="deepseek", call_type="summarize_chunk",
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

    # ── Memory consolidation ───────────────────────────────────────

    def consolidate_memory(self, existing_memory: str,
                           new_messages: list[dict]) -> str:
        """Update group memory by incorporating new messages.

        Uses Flash model for low cost and latency.  Returns the updated
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

        existing_display = existing_memory if existing_memory else "（暂无，这是第一次整理记忆）"

        system_prompt = MEMORY_CONSOLE_PROMPT.format(
            existing_memory=existing_display,
            new_messages="\n".join(msg_lines),
        )

        def call():
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请输出更新后的完整记忆日记。"},
                ],
            )
            text = response.choices[0].message.content or ""
            # Enforce 2000-char soft cap
            if len(text) > 2000:
                text = text[:2000]
            return text.strip()

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, "memory consolidation")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="deepseek", call_type="consolidate_memory",
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

    # ── Map-Reduce ────────────────────────────────────────────────

    def _merge_chunk_summaries(self, chunk_summaries: list[str],
                                requester_name: str) -> SummaryResult:
        """Merge chunk summaries into final structured result via tool calling."""
        user_prompt = build_merge_prompt(chunk_summaries, requester_name)

        def call():
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=8192,
                messages=[
                    {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[STORE_SUMMARY_TOOL],
                tool_choice="auto",
                extra_body=self.extra_body,
            )
            return _parse_summary_from_tool_call(response)

        start = time.monotonic()
        try:
            result = self._retry_with_backoff(call, "merge chunk summaries")
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="deepseek", call_type="merge_summaries",
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
