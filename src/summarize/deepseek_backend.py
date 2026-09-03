"""DeepSeek summarization backend.

DeepSeek API is OpenAI-compatible. Uses the openai Python SDK with
tool calling for structured output.

Base URL: https://api.deepseek.com
Docs: https://platform.deepseek.com/api-docs
"""

import json
import logging
import time
from typing import Iterator, Optional

from openai import OpenAI, RateLimitError, APIConnectionError, APIStatusError

from .base import AbstractSummarizer
from .errors import LLMContextOverflowError, LLMResponseError
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
        except (TypeError, ValueError):
            # ValueError 覆盖 json.JSONDecodeError 与 pydantic ValidationError
            # （两者都是 ValueError 子类）。LLM 返回 JSON 字段类型不符时
            # 降级到 Strategy 3 纯文本包裹，不能抛错中断摘要。
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

    # new-api 系中转在上下文超限时用的业务码
    _OVERFLOW_STATUS_CODES = frozenset({2013})
    _OVERFLOW_KEYWORDS = ("context window", "context length", "exceeds limit",
                          "too long", "maximum context")

    def _extract_choice(self, response, *, log_tag: str):
        """Safely return ``response.choices[0]``.

        Relay gateways answer context overflow with HTTP **200** and
        ``{"choices": null, "base_resp": {"status_code": 2013, ...}}``, so the
        SDK raises nothing and ``response.choices[0]`` used to blow up as
        ``TypeError: 'NoneType' object is not subscriptable`` — which then
        reached the task center as an undiagnosable error string.
        """
        choices = getattr(response, "choices", None)
        if choices:
            return choices[0]

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None

        base_resp = getattr(response, "base_resp", None)
        if base_resp is None:
            extra = getattr(response, "__pydantic_extra__", None) or {}
            base_resp = extra.get("base_resp")
        if isinstance(base_resp, dict):
            code = base_resp.get("status_code")
            msg = str(base_resp.get("status_msg") or "")
        else:
            code = getattr(base_resp, "status_code", None)
            msg = str(getattr(base_resp, "status_msg", "") or "")

        detail = f"（base_resp={code}: {msg}）" if (code is not None or msg) else ""
        text = (f"[{log_tag}] LLM 返回空 choices{detail}，model={self.model}，"
                f"prompt_tokens={prompt_tokens if prompt_tokens is not None else '未知'}")
        kwargs = dict(prompt_tokens=prompt_tokens, status_code=code,
                      status_msg=msg, raw=response)
        lowered = msg.lower()
        if code in self._OVERFLOW_STATUS_CODES or any(k in lowered for k in self._OVERFLOW_KEYWORDS):
            raise LLMContextOverflowError(text, **kwargs)
        raise LLMResponseError(text, **kwargs)

    def _call_with_thinking_guard(self, system_prompt: str,
                                  messages: list[dict],
                                  max_tokens: int = 4096,
                                  temperature: Optional[float] = None,
                                  log_tag: str = "CHAT-API",
                                  timeout: float | None = None) -> str:
        """调用 chat.completions，并在 thinking 模式耗尽 token 时自动降级重试。

        DeepSeek 推理模型（如 DeepSeek-V4-Flash-QC）在 thinking 模式下可能把
        max_tokens 全部花在 reasoning_content 上，导致最终 content 为空。

        降级采用双通道，兼容不同 API 对参数的支持差异：
          通道 1: thinking disabled + max_tokens 加倍（官方 DeepSeek 支持，最快）
          通道 2: 仅 max_tokens 加倍（不传 thinking 字段——部分中转 API 不认识
                 该字段会返回 400，捕获异常后走这里，仍能靠更多 token 让
                 reasoning 跑完并留出 content 空间）
        两层都失败才返回空，调用方自行兜底（如 "..."）。

        Args:
            timeout: 非 None 时作为 per-request 超时（秒）覆盖 client 级的 60s，
                三个通道都会带上。摘要这类长输出路径需要比对话更宽的窗口。

        Returns:
            content 字符串（可能为空，调用方自行兜底）。

        Raises:
            LLMContextOverflowError: 输入超出上下文窗口 —— 调用方应降级而不是重试。
            LLMResponseError: 响应体不可用但原因不是超限。
        """
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = self._merge_params(
            {"model": self.model, "max_tokens": max_tokens, "messages": api_messages},
            self.extra_body,
        )
        if temperature is not None:
            params["temperature"] = temperature
        if timeout:
            params["timeout"] = self._request_timeout(timeout)

        response = self.client.chat.completions.create(**params)
        choice = self._extract_choice(response, log_tag=log_tag)
        content = choice.message.content
        reasoning = getattr(choice.message, 'reasoning_content', None)

        # Graceful degradation: thinking mode consumed all tokens, leaving content=null.
        if not content and reasoning:
            retry_mt = max(max_tokens * 2, 4096)
            logger.warning(
                "[%s] thinking mode consumed all tokens (model=%s, max=%d, "
                "reasoning_chars=%d). Retrying with doubled max_tokens=%d.",
                log_tag, self.model, max_tokens, len(reasoning), retry_mt,
            )
            # 通道 1: 禁用 thinking（官方 API 支持；中转 API 可能 400 → 捕获降级）
            try:
                retry_kwargs = {
                    "model": self.model,
                    "max_tokens": retry_mt,
                    "messages": api_messages,
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
                if timeout:
                    retry_kwargs["timeout"] = self._request_timeout(timeout)
                response = self.client.chat.completions.create(**retry_kwargs)
                retry_content = self._extract_choice(
                    response, log_tag=log_tag + "-RETRY1").message.content
                if retry_content:
                    return retry_content
            except LLMContextOverflowError:
                # 输入太大，换通道只会更糟（同样的输入、更大的 max_tokens）。
                # 必须冒泡，否则调用方拿不到降级信号，而空 content 会变成 "..."
                # ——一个真值，会让任务被误判为成功。
                raise
            except Exception as retry_err:
                logger.warning(
                    "[%s] thinking-disabled retry unsupported (%s); "
                    "falling back to plain max_tokens bump", log_tag, retry_err,
                )
            # 通道 2: 仅加倍 max_tokens，不传 thinking（兼容不认识该字段的中转 API）
            try:
                retry_params = {
                    "model": self.model,
                    "max_tokens": retry_mt,
                    "messages": api_messages,
                }
                if temperature is not None:
                    retry_params["temperature"] = temperature
                if timeout:
                    retry_params["timeout"] = self._request_timeout(timeout)
                response = self.client.chat.completions.create(**retry_params)
                retry_content = self._extract_choice(
                    response, log_tag=log_tag + "-RETRY2").message.content
                if retry_content:
                    return retry_content
            except LLMContextOverflowError:
                raise
            except Exception as retry_err:
                logger.warning("[%s] max_tokens bump retry also failed: %s", log_tag, retry_err)

        return content

    def _call_chat_api(self, system_prompt: str,
                       messages: list[dict]) -> str:
        """DeepSeek-specific: uses chat.completions.create() with system role."""
        content = self._call_with_thinking_guard(
            system_prompt, messages, max_tokens=4096, log_tag="CHAT-API")
        if not content:
            logger.warning("[CHAT-API] LLM returned empty content (model=%s)", self.model)
            return "..."
        return content

    def _call_digest_api(self, system_prompt: str,
                         messages: list[dict],
                         timeout: float | None = None) -> str:
        """Digest-specific: higher max_tokens than chat for custom_prompt path."""
        content = self._call_with_thinking_guard(
            system_prompt, messages, max_tokens=4096, log_tag="DIGEST-API",
            timeout=timeout)
        if not content:
            logger.warning("[DIGEST-API] LLM returned empty content (model=%s)", self.model)
            return "..."
        return content

    def _call_long_api(self, system_prompt: str,
                       messages: list[dict],
                       max_tokens: int = 2000,
                       temperature: float = 0.3,
                       timeout: float | None = None) -> str:
        """Long-form API call with configurable params for OA digest etc."""
        content = self._call_with_thinking_guard(
            system_prompt, messages, max_tokens=max_tokens,
            temperature=temperature, log_tag="LONG-API", timeout=timeout)
        if not content:
            logger.warning("[LONG-API] LLM returned empty content (model=%s)", self.model)
            return "..."
        return content

    def _call_chat_api_stream(self, system_prompt: str,
                               messages: list[dict],
                               max_tokens: int = 2000,
                               extra_body: dict | None = None) -> Iterator[str]:
        """Stream chat API response, yielding token strings.

        extra_body: 调用方追加的 SDK 附加参数（如 {"thinking": {"type": "disabled"}}），
        以 OpenAI SDK 的 extra_body 参数传递，不平铺进顶层 kwargs
        （否则 SDK 报 unexpected keyword argument 错误）。
        """
        from typing import Iterator as _Iter
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "stream": True,
        }
        # config 的 AI_PROVIDER_EXTRA_BODY 也按 SDK extra_body 语义传递
        # （原来被 _merge_params 平铺成顶层参数，配置了附加参数会直接报错）
        sdk_extra = dict(self.extra_body or {})
        if extra_body:
            sdk_extra.update(extra_body)
        if sdk_extra:
            params["extra_body"] = sdk_extra
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
            {"model": self.model, "max_tokens": 4096,
             "messages": api_messages,
             "tools": tools, "tool_choice": "auto"},
            self.extra_body,
        )

        start = time.monotonic()
        try:
            response = self._retry_with_backoff(
                lambda: self.client.chat.completions.create(**params),
                "agent chat",
            )
            latency = (time.monotonic() - start) * 1000
        except Exception:
            latency = (time.monotonic() - start) * 1000
            logger.info("[LLM] agent_chat FAILED after %.1fms", latency)
            raise

        msg = self._extract_choice(response, log_tag="AGENT-CHAT").message
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

        # Extract reasoning_content (DeepSeek thinking mode).
        # Must be preserved in conversation history for tool-calling rounds,
        # otherwise upstream API returns 400 (see DeepSeek docs).
        reasoning = getattr(msg, "reasoning_content", None) or ""

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
                   "messages": len(messages),
                   "reasoning_content": reasoning},
        )

        return content, tool_calls, reasoning

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
            text = self._extract_choice(response, log_tag="MEMORY").message.content or ""
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
