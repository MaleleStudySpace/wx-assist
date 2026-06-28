"""Operation logger — structured logging for key operations and flows.

Provides tagged log methods that write structured, filterable entries
to the standard Python logger. The frontend LogViewer can filter by
operation tag (e.g. [KEY-EXTRACT], [MSG-POLL], [SEND], [API], etc.).

Usage:
    from src.utils.op_logger import op_log

    op_log("KEY-EXTRACT", "Hook 安装成功, PID=%d", pid)
    op_log("MSG-POLL", "收到 %d 条新消息, group=%s", count, group_name)
    op_log("SEND", "消息发送成功, group=%s len=%d", group, len(text))
    op_log("API", "GET /api/fav/list → %d items, %.0fms", count, elapsed)
"""

import logging
import threading
import time
from functools import wraps

logger = logging.getLogger(__name__)

# ── Tag definitions ────────────────────────────────────────────────
# Each tag maps to a category for frontend filtering.
# Tags are short uppercase identifiers wrapped in [brackets].

TAG_CATEGORIES = {
    # Core lifecycle
    "BOOT":       "启动",
    "KEY-EXTRACT": "密钥提取",
    "DB":         "数据库",

    # Message flow
    "MSG-POLL":   "消息轮询",
    "MSG-RECV":   "收到消息",
    "MSG-DEDUP":  "消息去重",
    "SEND":       "发送消息",
    "SEND-FAIL":  "发送失败",

    # AI / LLM
    "LLM":        "LLM调用",
    "AI-CHAT":    "AI对话",

    # Proactive
    "PROACTIVE":  "主动发言",

    # API operations
    "API":        "API请求",
    "EXPORT":     "导出操作",

    # Assistant
    "ALERT":      "关键词告警",
    "DIGEST":     "摘要生成",

    # Window control
    "WND":        "窗口控制",
    "HOOK":       "Hook操作",
}

# Thread-local storage for operation timing
_local = threading.local()


def op_log(tag: str, fmt: str, *args, level: int = logging.INFO, **kwargs):
    """Log a structured operation entry.

    Args:
        tag: Operation tag (e.g. "MSG-POLL", "SEND", "API").
        fmt: Log message format string.
        *args: Format arguments.
        level: Log level (default INFO).
    """
    msg = fmt % args if args else fmt
    tagged_msg = f"[{tag}] {msg}"
    logger.log(level, tagged_msg, **kwargs)


def op_log_debug(tag: str, fmt: str, *args):
    """Log at DEBUG level."""
    op_log(tag, fmt, *args, level=logging.DEBUG)


def op_log_warning(tag: str, fmt: str, *args):
    """Log at WARNING level."""
    op_log(tag, fmt, *args, level=logging.WARNING)


def op_log_error(tag: str, fmt: str, *args):
    """Log at ERROR level."""
    op_log(tag, fmt, *args, level=logging.ERROR)


# ── Timing helpers ─────────────────────────────────────────────────

def op_start(tag: str, operation: str) -> str:
    """Mark the start of a timed operation. Returns an op_id for op_end()."""
    op_id = f"{tag}_{id(threading.current_thread())}_{time.monotonic_ns()}"
    if not hasattr(_local, 'timings'):
        _local.timings = {}
    _local.timings[op_id] = {
        'tag': tag,
        'operation': operation,
        'start': time.monotonic(),
    }
    return op_id


def op_end(op_id: str, success: bool = True, detail: str = ""):
    """Mark the end of a timed operation and log the duration."""
    if not hasattr(_local, 'timings'):
        return
    info = _local.timings.pop(op_id, None)
    if not info:
        return
    elapsed_ms = (time.monotonic() - info['start']) * 1000
    status = "OK" if success else "FAIL"
    detail_str = f" | {detail}" if detail else ""
    elapsed_str = f"{elapsed_ms / 1000:.1f}s" if elapsed_ms >= 1000 else f"{elapsed_ms:.0f}ms"
    op_log(
        info['tag'],
        "%s %s %s%s",
        info['operation'], status, elapsed_str, detail_str,
        level=logging.INFO if success else logging.WARNING,
    )


# ── Decorator for API handler timing ───────────────────────────────

def log_api(endpoint: str):
    """Decorator that logs API handler entry, timing, and result.

    Usage:
        @log_api("/api/fav/list")
        def handle_fav_list(params, config):
            ...
            return {"ok": True, "data": result}
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                elapsed_str = f"{elapsed_ms / 1000:.1f}s" if elapsed_ms >= 1000 else f"{elapsed_ms:.0f}ms"

                # Extract result info
                ok = result.get("ok", False) if isinstance(result, dict) else True
                # Count items if present
                count = ""
                if isinstance(result, dict):
                    for key in ("favorites", "items", "sessions", "messages",
                                "timeline", "articles", "groups", "notifications"):
                        val = result.get(key)
                        if isinstance(val, list):
                            count = f" → {len(val)} items"
                            break
                    total = result.get("total")
                    if total is not None:
                        count = f" → {total} total"

                status = "OK" if ok else "FAIL"
                error = ""
                if not ok and isinstance(result, dict):
                    error = f" | {result.get('error', 'unknown')[:80]}"

                op_log(
                    "API",
                    "%s %s %s%s%s",
                    endpoint, status, elapsed_str, count, error,
                    level=logging.INFO if ok else logging.WARNING,
                )
                return result
            except Exception as e:
                elapsed_ms = (time.monotonic() - start) * 1000
                elapsed_str = f"{elapsed_ms / 1000:.1f}s" if elapsed_ms >= 1000 else f"{elapsed_ms:.0f}ms"
                op_log_error("API", "%s FAIL %s | %s", endpoint, elapsed_str, str(e)[:80])
                raise
        return wrapper
    return decorator
