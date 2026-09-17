"""Abstract base class for AI summarization backends.

Implementations: ClaudeSummarizer, OpenAICompatSummarizer.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterator, TypeVar

from ..utils.llm_logger import log_llm_interaction, mask_secrets

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 失败交互日志里错误串的长度上限 —— 中转网关偶尔会把整个请求体回显进错误信息。
_ERROR_CLIP_CHARS = 600


def _clip(text: str, limit: int = _ERROR_CLIP_CHARS) -> str:
    """截断错误串，但保留原始长度，避免日志被超长响应撑爆。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(共 {len(text)} 字)"


def _exc_chain(exc: BaseException) -> Iterator[BaseException]:
    """沿 ``__cause__`` / ``__context__`` 回溯异常链（带去重，防自引用死循环）。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = (getattr(current, "__cause__", None)
                   or getattr(current, "__context__", None))


def _error_diagnostics(exc: BaseException) -> dict[str, Any]:
    """从 LLM 异常链里提取可定位、已脱敏的标量诊断字段。

    覆盖线上实际出现的三种形态：
      - openai ``APIStatusError`` → ``status_code`` / ``response.status_code``
      - ``LLMResponseError``（中转 HTTP 200 + ``choices: null``）
        → ``status_code`` / ``status_msg`` / ``prompt_tokens``
      - 连接类失败 → 没有状态码，但有 ``cause_type``（如 ConnectError）
    """
    info: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error": mask_secrets(_clip(str(exc))),
    }
    for err in _exc_chain(exc):
        status = getattr(err, "status_code", None)
        if not isinstance(status, int):
            response = getattr(err, "response", None)
            status = getattr(response, "status_code", None)
        if isinstance(status, int) and "status_code" not in info:
            info["status_code"] = status

        status_msg = getattr(err, "status_msg", None)
        if status_msg and "status_msg" not in info:
            info["status_msg"] = mask_secrets(_clip(str(status_msg), 300))

        prompt_tokens = getattr(err, "prompt_tokens", None)
        if isinstance(prompt_tokens, int) and "prompt_tokens" not in info:
            info["prompt_tokens"] = prompt_tokens

        if err is not exc and "cause_type" not in info:
            info["cause_type"] = type(err).__name__
            info["cause"] = mask_secrets(_clip(str(err), 300))
    return info


class AbstractSummarizer(ABC):
    """Abstract summarizer — shared retry/timeout helpers plus the call contract.

    Subclasses must implement:
      - _call_chat_api(system_prompt, messages) -> str
      - _call_digest_api(system_prompt, messages, timeout) -> str
      - _call_long_api(system_prompt, messages, max_tokens, temperature, timeout) -> str
      - _call_chat_api_stream(system_prompt, messages, max_tokens) -> Iterator[str]
      - agent_chat(system_prompt, messages, tools) -> (content, tool_calls, reasoning)
      - consolidate_memory(existing_memory, new_messages) -> str

    They may override:
      - retry_exceptions (tuple of exception types to retry on)

    `token_budget` and `chunk_size` no longer drive any logic in this class —
    the map-reduce summary chain they fed is gone.  They are kept because
    `src/web/ai_chat.py` reads `token_budget` for its context-compression
    thresholds and `BotConfig.chunk_size` (env CHUNK_SIZE) is validated and
    forwarded into every backend constructor.
    """

    # Override in subclass
    token_budget: int = 100_000
    chunk_size: int = 400
    max_retries: int = 3
    retry_exceptions: tuple = ()
    _backend_name: str = "unknown"  # Override in subclass: "deepseek" | "claude"

    # Health monitoring: track last successful API call timestamp
    last_api_call_time: float = 0.0

    # ── Generic LLM call (prompt-driven, no tool calling) ─────────────

    # Chat prompt template — supports {placeholders}
    CHAT_SYSTEM_PROMPT = """\
你是 AI 助手，根据用户的请求提供帮助。

## 身份
- 你是 AI 程序，不是真人。
- 如果问你是谁写的 → "开发者写的"。

## 说话风格
- 简洁自然，用中文回复。
- 先抛结论，有必要再补充细节。
- 可以适度使用表情。

## 回复规则
- 信息不够就反问，不要硬编。
- 不要做危险/违法/侵犯隐私的事。

## 当前上下文
时间：{current_time}
主题：{group_name}
发言人：{sender_name}

{context_section}用户消息：
{current_message}"""

    def chat(self, message: str,
             context_messages: list[dict] | None = None,
             requester_name: str = "",
             group_name: str = "对话") -> str:
        """AI chat call (prompt-driven, no tool calling).

        Provides a generic LLM text generation entry point.
        Used by digest generation, OA article summarization, and sandbox testing.

        Args:
            message: The user's message or prompt.
            context_messages: Optional recent chat history for context.
            requester_name: Name of the requester (for logging).
            group_name: Chat/group name context (for system prompt).

        Returns:
            AI response text.
        """
        import datetime

        # ── Defense-in-depth: escape curly braces in all user-supplied
        #     strings so they don't break str.format() below.
        def _esc(s: str) -> str:
            return s.replace("{", "{{").replace("}", "}}")

        group_name = _esc(group_name)
        requester_name = _esc(requester_name or "用户")
        message = _esc(message)

        # ── 1. Build context section ───────────────────────────────
        context_section = ""
        if context_messages and len(context_messages) > 0:
            context_lines = []
            for m in context_messages[-20:]:
                sender = m.get("sender_name", "?")
                content = m.get("content", "")
                if content:
                    context_lines.append(f"{sender}: {content}")
            if context_lines:
                context_section = (
                    "最近记录：\n"
                    + "\n".join(context_lines)
                    + "\n\n"
                )

        # ── 2. Build full system prompt ────────────────────────────
        context_section = _esc(context_section)

        system_prompt = self.CHAT_SYSTEM_PROMPT.format(
            group_name=group_name,
            sender_name=requester_name or "用户",
            current_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            context_section=context_section,
            current_message=message,
        )

        # ── 3. Build user message ──────────────────────────────────
        user_prompt = (
            f"{requester_name or '用户'}：{message}"
        )

        # ── 4. Call AI API (backend-specific) ─────────────────────
        start = time.monotonic()
        try:
            result = self._retry_with_backoff(
                lambda: self._call_chat_api(
                    system_prompt,
                    [{"role": "user", "content": user_prompt}],
                ),
                "AI chat",
            )
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend=self._backend_name,
                call_type="chat",
                model=getattr(self, 'model', 'unknown'),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response=result,
                latency_ms=latency,
                extra={"requester": requester_name, "group": group_name},
            )
            return result
        except RuntimeError as e:
            latency = (time.monotonic() - start) * 1000
            logger.warning(
                "[LLM] chat FAILED after %.1fms (%s: %s)",
                latency, type(e).__name__, e,
            )
            # 成功路径本来就写 data/llm.log；失败路径此前只写 bot.log 一行
            # "chat FAILED"，导致上游错误码、异常类型全部丢失。这里补齐失败
            # 交互记录：response 以 "[Error:" 开头 → 日志状态标 FAILED，
            # extra 带异常类型 / HTTP 状态码 / provider 业务码 / token 数。
            log_llm_interaction(
                backend=self._backend_name,
                call_type="chat",
                model=getattr(self, 'model', 'unknown'),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response=f"[Error: {type(e).__name__}: {_clip(str(e))}]",
                latency_ms=latency,
                extra={
                    "requester": requester_name,
                    "group": group_name,
                    **_error_diagnostics(e),
                },
            )
            raise

    # ⚠ DEAD CODE REMOVED: proactive_chat() and PROACTIVE_SYSTEM_PROMPT
    # were removed along with src/proactive/ (disabled-features cleanup).
    # The abstract methods below remain for the summarizer contract.

    @abstractmethod
    def _call_chat_api(self, system_prompt: str,
                        messages: list[dict]) -> str:
        """Execute the chat API call. Backend-specific.

        Claude backend: uses client.messages.create() with system param.
        DeepSeek backend: uses client.chat.completions.create() with
                          system role in messages list.
        """
        ...

    @staticmethod
    def _request_timeout(seconds: float):
        """Per-request timeout widening read/write/pool but keeping connect at 10s.

        Passing a bare float to the SDK would also stretch the connect timeout,
        making "host unreachable" take the full duration to fail.
        """
        import httpx
        return httpx.Timeout(float(seconds), connect=10.0)

    @abstractmethod
    def _call_digest_api(self, system_prompt: str,
                         messages: list[dict],
                         timeout: float | None = None) -> str:
        """Execute digest API call with higher max_tokens than chat.

        Used for custom_prompt digest generation where output needs
        to be much longer than a brief chat reply.

        Args:
            timeout: 非 None 时作为 per-request 超时（秒）覆盖 client 级默认值。
                摘要输出长、耗时由输出长度决定，需要比对话更宽的窗口。
        """
        ...

    @abstractmethod
    def _call_long_api(self, system_prompt: str,
                       messages: list[dict],
                       max_tokens: int = 2000,
                       temperature: float = 0.3,
                       timeout: float | None = None) -> str:
        """Execute a long-form API call with configurable params.

        Used for OA digest and other non-chat, non-summary LLM calls
        that need higher max_tokens and custom temperature.
        """
        ...

    @abstractmethod
    def _call_chat_api_stream(self, system_prompt: str,
                               messages: list[dict],
                               max_tokens: int = 2000) -> Iterator[str]:
        """Stream chat API response, yielding token strings one by one.

        Used by the AI Chat feature (favorites & group chat) for SSE
        streaming to the frontend.

        Claude backend: uses client.messages.stream() with .text_stream.
        DeepSeek backend: uses client.chat.completions.create(stream=True).
        """
    # ── Agent chat with tool calling ─────────────────────────────────

    @abstractmethod
    def agent_chat(self, system_prompt: str,
                   messages: list[dict],
                   tools: list[dict]) -> tuple[str, list[dict] | None, str]:
        """ReAct Agent chat with tool calling support.

        LLM may reply directly (content, None) or request tool calls (None, tool_calls).

        Args:
            system_prompt: Agent system prompt.
            messages: OpenAI-format conversation (may contain tool_calls/tool_call_id).
            tools: OpenAI-format tool definitions.

        Returns:
            (content, tool_calls, reasoning_content):
            - content: str | None — text reply
            - tool_calls: list[dict] | None — OpenAI-format tool calls
              each: {"id", "type", "function": {"name", "arguments"}}
            - reasoning_content: str — thinking/chain-of-thought content
              (DeepSeek reasoning models); empty string if not supported.
        """
        ...

    # ── Abstract methods ──────────────────────────────────────────

    @abstractmethod
    def consolidate_memory(self, existing_memory: str,
                           new_messages: list[dict]) -> str:
        """Update group memory by incorporating new messages.

        Must be implemented by every backend so that memory consolidation
        works regardless of which AI provider is configured.

        Args:
            existing_memory: Current memory text (empty string if first time).
            new_messages: List of new message dicts to incorporate.

        Returns:
            Updated first-person diary-style memory text, or existing_memory
            unchanged on failure.
        """
        ...

    # ── Shared helpers ────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        """Estimate total token count for a list of messages.

        Conservative heuristic: ~1.5 characters per token (Chinese-heavy text),
        plus XML overhead (~40 chars per message), plus system prompt (~500).
        """
        total_chars = 0
        for msg in messages:
            sender = msg.get("sender_name", "")
            content = msg.get("content", "")
            total_chars += len(sender) + len(content) + 40
        return int(total_chars / 1.5) + 500

    def _retry_with_backoff(self, call_fn: Callable[[], T],
                             label: str) -> T:
        """Execute call_fn with retry + exponential backoff.

        Args:
            call_fn: Zero-argument callable that makes the API request.
            label: Human-readable label for logging.

        Returns:
            The return value of call_fn().

        Raises:
            RuntimeError: If all retries are exhausted.
        """
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = call_fn()
                self.last_api_call_time = time.time()
                return result
            except self.retry_exceptions as e:
                wait = 2 ** attempt
                logger.warning(
                    "Transient error on '%s' (attempt %d/%d). "
                    "Waiting %ds... (%s)",
                    label, attempt, self.max_retries, wait, e,
                )
                time.sleep(wait)
                last_error = e

        error = RuntimeError(
            f"Failed after {self.max_retries} retries on '{label}': "
            f"{last_error}"
        )
        # 保留原始异常为 __cause__：否则重试耗尽后 HTTP 状态码 / provider
        # 业务码只剩错误串，失败交互日志无法结构化记录。
        if last_error is not None:
            raise error from last_error
        raise error
