"""LLM interaction logger — records full request/response for observability.

Writes to a dedicated log file (data/llm.log) separate from the main bot.log,
so LLM traffic can be inspected independently.

Two log lines per LLM call:
  1. [LLM] summary line — compact, always visible in the log viewer
  2. [LLM-DETAIL] JSON line — full prompts + response, parsed by frontend
     for collapsible display.

Thread-safe.  API keys are masked before logging.
"""

import json
import logging
import re
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Dedicated LLM log file ─────────────────────────────────────────────
# Separate from the main bot.log so LLM traffic can be reviewed independently.

_LLM_LOG_PATH = Path("data/llm.log")
_LLM_LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
_LLM_LOG_BACKUP_COUNT = 3                # keep 3 rotated files

_llm_logger: logging.Logger | None = None
_llm_logger_lock = threading.Lock()


def _get_llm_logger() -> logging.Logger:
    """Get or create the dedicated LLM logger with its own file handler."""
    global _llm_logger
    if _llm_logger is not None:
        return _llm_logger

    with _llm_logger_lock:
        if _llm_logger is not None:
            return _llm_logger

        lg = logging.getLogger("llm")
        lg.setLevel(logging.DEBUG)
        lg.propagate = False  # Don't duplicate to root logger / bot.log

        # Ensure data/ exists
        _LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        handler = RotatingFileHandler(
            str(_LLM_LOG_PATH),
            maxBytes=_LLM_LOG_MAX_BYTES,
            backupCount=_LLM_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fmt = "%(asctime)s %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        handler.setFormatter(logging.Formatter(fmt, datefmt))
        lg.addHandler(handler)

        _llm_logger = lg
        return _llm_logger


# Also keep a module-level logger for the summary line (goes to bot.log too)
logger = logging.getLogger(__name__)

# ── Thread-safe interaction counter ──────────────────────────────────────
_counter = 0
_counter_lock = threading.Lock()


def _next_id() -> str:
    global _counter
    with _counter_lock:
        _counter += 1
        ts = time.strftime("%Y%m%d_%H%M%S")
        return f"llm_{ts}_{_counter:04d}"


# ── Secret masking ──────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    # Bearer tokens:  Authorization: Bearer sk-xxxxx
    (re.compile(r'(Bearer\s+)\S+', re.IGNORECASE), r'\1***'),
    # OpenAI-style:   sk-xxxxxxxxxxxx
    (re.compile(r'(sk-)\S+'), r'\1***'),
    # Anthropic-style: sk-ant-xxxxx
    (re.compile(r'(sk-ant-)\S+'), r'\1***'),
    # Generic api_key=xxx / apikey=xxx
    (re.compile(r'(api[_-]?key\s*[=:]\s*["\']?)\S+', re.IGNORECASE), r'\1***'),
]


def _mask_secrets(text: str) -> str:
    """Mask API keys and Bearer tokens in text before logging."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(text: str, max_len: int = 0) -> str:
    """Truncate text if max_len > 0 and text exceeds it."""
    if max_len > 0 and len(text) > max_len:
        return text[:max_len] + f"...({len(text)} chars total)"
    return text


# ── Core logging function ───────────────────────────────────────────────

def log_llm_interaction(
    backend: str,
    call_type: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response: str,
    latency_ms: float,
    token_in: int = 0,
    token_out: int = 0,
    extra: dict | None = None,
) -> str:
    """Log an LLM interaction with both summary and detail lines.

    Args:
        backend: "deepseek" | "claude" | "oa_digest"
        call_type: "chat" | "proactive_chat" | "summarize_direct" |
                   "summarize_chunk" | "merge_summaries" |
                   "consolidate_memory" | "oa_digest" |
                   "group_digest" | "group_digest_memory" |
                   "ai_chat_stream" | "ai_chat_compress" |
                   "ai_chat_precompress" | "sns_ai_summarize" |
                   "sns_precompress" | "provider_detect"
        model: Model identifier string.
        system_prompt: Full system prompt sent to the LLM.
        user_prompt: Full user prompt sent to the LLM.
        response: Full LLM response text.
        latency_ms: Round-trip latency in milliseconds.
        token_in: Input token count (0 if unavailable).
        token_out: Output token count (0 if unavailable).
        extra: Optional dict with extra context (requester, group, etc.).

    Returns:
        The interaction ID for cross-referencing.
    """
    interaction_id = _next_id()

    # Mask secrets in prompts (responses shouldn't contain keys, but mask anyway)
    safe_sys = _mask_secrets(system_prompt)
    safe_user = _mask_secrets(user_prompt)
    safe_resp = _mask_secrets(response)

    # ── Line 1: Compact summary ───────────────────────────────────
    is_error = safe_resp.startswith("[Error:")
    token_info = ""
    if token_in or token_out:
        token_info = f" | {token_in}→{token_out} tokens"

    resp_preview = _truncate(safe_resp.strip(), 80).replace("\n", " ")
    latency_str = f"{latency_ms / 1000:.1f}s" if latency_ms >= 1000 else f"{latency_ms:.0f}ms"

    status = "FAILED" if is_error else "OK"
    summary = (
        f"[LLM] {call_type} | {backend}/{model}{token_info} "
        f"| {latency_str} | {status} | resp: {resp_preview}"
    )
    logger.info(summary)

    # ── Line 2: Full detail JSON ──────────────────────────────────
    detail = {
        "id": interaction_id,
        "backend": backend,
        "call_type": call_type,
        "model": model,
        "system_prompt": safe_sys,
        "user_prompt": safe_user,
        "response": safe_resp,
        "latency_ms": round(latency_ms, 1),
        "token_in": token_in,
        "token_out": token_out,
    }
    if extra:
        detail["extra"] = extra

    # json.dumps with ensure_ascii=True to avoid encoding issues when
    # the log file is read back by the server and re-serialized as JSON.
    # Chinese characters become \uXXXX escapes which are universally safe.
    detail_json = json.dumps(detail, ensure_ascii=True)

    # Write detail to BOTH the main bot.log AND the dedicated llm.log
    logger.info("[LLM-DETAIL] %s", detail_json)
    _get_llm_logger().info("[LLM-DETAIL] %s", detail_json)

    return interaction_id
