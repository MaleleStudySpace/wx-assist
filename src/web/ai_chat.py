"""AI Chat session management and API handlers.

Provides in-memory session storage with TTL expiry, context building
from favorites and group chat messages, SSE streaming, and auto-compression.
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator

from .api_handlers import (
    _get_wcdb_fav_reader,
    get_wcdb_client,
    _decompress_content,
    _strip_wxid_prefix,
)
from ..summarize import create_summarizer
from ..summarize.prompts import (
    FAV_CHAT_SYSTEM_PROMPT,
    GROUP_CHAT_SYSTEM_PROMPT,
    PRIVATE_CHAT_SYSTEM_PROMPT,
    SNS_CHAT_SYSTEM_PROMPT,
    SNS_SUMMARY_PROMPT,
    COMPRESSION_PROMPT,
)
from ..config import load_config
from ..utils.llm_logger import log_llm_interaction
from ..utils.op_logger import op_log, op_log_error

logger = logging.getLogger(__name__)

# ── Safety limits for context building ────────────────────────────
# Prevent OOM crash when processing groups with huge message history.
# Chinese text ≈ 1.5 chars/token; 200K chars ≈ 133K tokens — well
# within typical context budgets while being safe for memory.
MAX_CONTEXT_CHARS = 200_000      # hard cap on formatted context
MAX_SINGLE_MSG_CHARS = 2000     # truncate any single message
MAX_DECOMPRESS_SIZE = 500_000   # 500KB per-message decompress limit (vs default 10MB)

# ── Session storage ────────────────────────────────────────────────

_sessions: dict[str, "AIChatSession"] = {}
_sessions_lock = threading.Lock()
SESSION_TTL = 1800  # 30 minutes


@dataclass
class AIChatSession:
    session_id: str
    source_type: str          # "favorites" | "group_chat"
    source_id: str            # "" for favorites, chatroom wxid for group
    source_name: str          # display name
    context_text: str         # formatted context fed to AI
    context_tokens: int       # estimated tokens of context_text
    chat_history: list        # [{"role", "content", "timestamp"}]
    estimated_tokens: int     # running total (context + history)
    token_budget: int         # max tokens for this session
    created_at: float
    last_active: float


def _cleanup_expired():
    """Remove sessions older than SESSION_TTL."""
    with _sessions_lock:
        now = time.time()
        expired = [
            sid for sid, s in _sessions.items()
            if now - s.last_active > SESSION_TTL
        ]
        for sid in expired:
            del _sessions[sid]
    if expired:
        logger.info("Cleaned up %d expired AI chat sessions", len(expired))


def _get_session(session_id: str) -> AIChatSession | None:
    """Get session by ID, checking TTL."""
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            return None
        if time.time() - session.last_active > SESSION_TTL:
            del _sessions[session_id]
            return None
        return session


def _estimate_tokens(text: str) -> int:
    """Rough token estimation for Chinese-heavy text."""
    return int(len(text) / 1.5) + 200


def _send_sse_event(wfile, event: str, data: dict):
    """Write a single SSE event and flush."""
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    try:
        wfile.write(payload.encode("utf-8"))
        wfile.flush()
    except BrokenPipeError:
        logger.info("Client disconnected during SSE stream")
        raise
    except ConnectionResetError:
        logger.info("Client reset connection during SSE stream")
        raise


# ── Context builders ──────────────────────────────────────────────

def _build_sns_context(limit: int = 50, username: str = "") -> tuple[str, int, str, int]:
    """Build context text from WeChat Moments (朋友圈).

    Args:
        limit: Max number of posts to include.
        username: If provided, only include posts from this user.

    Returns (context_text, estimated_tokens, source_name, actual_post_count).
    """
    from .api_handlers import _get_wcdb_sns_reader
    reader = _get_wcdb_sns_reader()
    if not reader:
        return "", 0, "朋友圈"

    try:
        usernames = [username] if username else None
        posts = reader.get_timeline(limit=limit, usernames=usernames)
    except Exception as e:
        logger.error("Failed to read SNS timeline: %s", e)
        return "", 0, "朋友圈", 0

    if not posts:
        return "", 0, "朋友圈", 0

    # Format posts into context lines
    lines = []
    total_chars = 0
    for i, post in enumerate(posts, 1):
        # Support both dict and dataclass (SnsPost)
        def _get(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        nickname = _get(post, "nickname", "未知")
        # Raw DLL data uses contentDesc; timeline endpoint maps to content
        content = _get(post, "content", "") or _get(post, "contentDesc", "")
        create_time = _get(post, "create_time", 0) or _get(post, "createTime", 0)

        # Format timestamp
        if create_time:
            import datetime as _dt
            try:
                ts = _dt.datetime.fromtimestamp(create_time).strftime("%m/%d %H:%M")
            except Exception:
                ts = ""
        else:
            ts = ""

        # Build line
        line_parts = [f"{i}. "]
        if ts:
            line_parts.append(f"[{ts}] ")
        line_parts.append(f"{nickname}: ")

        if content:
            # Truncate long content
            if len(content) > MAX_SINGLE_MSG_CHARS:
                content = content[:MAX_SINGLE_MSG_CHARS] + "..."
            line_parts.append(content)
        else:
            # No text content — describe media if present
            media_list = _get(post, "media_list", []) or _get(post, "media", [])
            if media_list:
                img_count = sum(1 for m in media_list if _get(m, "type", "") in ("1", "2", "image"))
                video_count = sum(1 for m in media_list if _get(m, "type", "") in ("4", "8", "video"))
                parts = []
                if img_count:
                    parts.append(f"{img_count}张图片")
                if video_count:
                    parts.append(f"{video_count}个视频")
                if parts:
                    line_parts.append(f"[{' '.join(parts)}]")
                else:
                    line_parts.append("[媒体内容]")
            else:
                line_parts.append("[无文字内容]")

        # Add interaction info
        likes_raw = _get(post, "likes", [])
        comments_raw = _get(post, "comments", [])
        like_count = _get(post, "like_count", 0)
        if not like_count and likes_raw:
            like_count = len(likes_raw) if isinstance(likes_raw, list) else 0
        comment_count = _get(post, "comment_count", 0)
        if not comment_count and comments_raw:
            comment_count = len(comments_raw) if isinstance(comments_raw, list) else 0
        media_list = _get(post, "media_list", []) or _get(post, "media", [])
        media_count = len(media_list) if media_list else 0
        extras = []
        if like_count:
            extras.append(f"{like_count}赞")
        if comment_count:
            extras.append(f"{comment_count}评论")
        if media_count:
            extras.append(f"{media_count}张图/视频")
        if extras:
            line_parts.append(f" ({', '.join(extras)})")

        line = "".join(line_parts)
        if total_chars + len(line) + 1 > MAX_CONTEXT_CHARS:
            lines.append(f"...(共 {len(posts)} 条朋友圈，更多已省略)")
            break
        lines.append(line)
        total_chars += len(line) + 1

    context = "\n".join(lines)
    tokens = _estimate_tokens(context)
    first_nickname = _get(posts[0], "nickname", "") if posts else ""
    source_name = "朋友圈" if not username else (f"朋友圈·{first_nickname}" if first_nickname else "朋友圈")
    return context, tokens, source_name, len(posts)

def _flatten_chat_records_text(records: list, depth: int = 0, max_depth: int = 5) -> list[str]:
    """Recursively flatten chat records into text lines for LLM context.

    Supports nested type=17 (chat record inside chat record) up to max_depth.
    Each type is rendered with a descriptive label instead of the generic [媒体].
    """
    texts = []
    for r in records:
        r_type = int(r.get("type", 0) or 0)
        name = r.get("src_name", "未知")
        if r_type == 1 and r.get("desc"):
            content = r["desc"]
            if len(content) > MAX_SINGLE_MSG_CHARS:
                content = content[:MAX_SINGLE_MSG_CHARS] + "..."
            texts.append(f"{name}: {content}")
        elif r_type == 2:
            texts.append(f"{name}: [图片]")
        elif r_type == 3:
            texts.append(f"{name}: [语音]")
        elif r_type == 8:
            fname = r.get("file_name", "") or r.get("desc", "") or ""
            fext = r.get("file_type", "")
            label = fname
            if fext and not fname.endswith(f".{fext}"):
                label = f"{fname}.{fext}" if fname else fext
            texts.append(f"{name}: [文件] {label}" if label else f"{name}: [文件]")
        elif r_type == 17:
            if depth < max_depth:
                sub = r.get("sub_records", [])
                if sub:
                    sub_texts = _flatten_chat_records_text(sub, depth + 1, max_depth)
                    texts.append(f"{name}: [嵌套聊天] {'; '.join(sub_texts)}")
                else:
                    texts.append(f"{name}: [嵌套聊天]")
            else:
                texts.append(f"{name}: [嵌套聊天(过深)]")
    return texts


def _build_favorites_context(tag_id: str | None = None,
                              fav_types: list[int] | None = None) -> tuple[str, int]:
    """Build context text from favorites.

    Args:
        tag_id: If provided, only include favorites with this tag.
        fav_types: If provided, only include these favorite types
                   (e.g. [1, 14, 5, 33]). Default: [1, 14, 5, 33].

    Returns (context_text, estimated_tokens).
    """
    reader = _get_wcdb_fav_reader()
    if not reader:
        return "", 0

    # Default type set: text, chat record, link, article
    type_set = set(fav_types) if fav_types else {1, 14, 5, 33}

    try:
        items = reader.get_items(limit=5000)
    except Exception as e:
        logger.error("Batch read favorites failed: %s, falling back to per-item load", e)
        # Fallback: query metadata without content (always safe), then load content per-item
        safe_sql = (
            "SELECT local_id, type, update_time, fromusr "
            "FROM fav_db_item ORDER BY update_time DESC LIMIT 5000"
        )
        safe_rows = reader._exec(safe_sql) or []
        items = []
        for r in safe_rows:
            item = reader._parse_fav_row(r)
            lid = r["local_id"]
            try:
                full = reader.get_by_id(lid)
                if full:
                    item.update(full)
            except Exception:
                logger.warning("Fav content load failed for local_id=%s in AI context", lid)
            items.append(item)

    # ── Tag filter ──
    if tag_id:
        try:
            bindings = reader.get_tag_bindings() or []
            allowed_ids = {
                b["fav_local_id"]
                for b in bindings
                if b.get("tag_local_id") == tag_id
            }
            items = [i for i in items if str(i.get("local_id", "")) in allowed_ids]
        except Exception as e:
            logger.warning("Failed to apply tag filter: %s", e)

    # ── Type filter ──
    items = [i for i in items if int(i.get("type", 0) or 0) in type_set]

    lines = []
    total_chars = 0
    for i, item in enumerate(items, 1):
        fav_type = int(item.get("type", 0) or 0)
        line = None
        if fav_type == 1:  # 文字
            # _parse_fav_xml stores text content under "description" (XML <desc>)
            content = item.get("description") or item.get("content", "") or ""
            if len(content) > MAX_SINGLE_MSG_CHARS:
                content = content[:MAX_SINGLE_MSG_CHARS] + "..."
            if content:
                line = f"{i}. [文字] {content}"
        elif fav_type == 14:  # 聊天记录
            # Flatten chat_records text (recursive — supports nested type=17)
            chat_records = item.get("chat_records", [])
            if chat_records:
                record_texts = _flatten_chat_records_text(chat_records)
                if record_texts:
                    line = f"{i}. [聊天记录] {' | '.join(record_texts)}"
            else:
                # Note without chat_records — try content field
                content = item.get("content", "") or ""
                if len(content) > MAX_SINGLE_MSG_CHARS:
                    content = content[:MAX_SINGLE_MSG_CHARS] + "..."
                if content:
                    line = f"{i}. [笔记] {content}"
        elif fav_type == 5:  # 链接
            title = item.get("title", "") or item.get("content", "") or ""
            link = item.get("link", "") or ""
            if title or link:
                line = f"{i}. [链接] {title} — {link}"
        elif fav_type == 2:  # 图片
            title = item.get("title", "") or item.get("content", "") or ""
            line = f"{i}. [图片] {title}" if title else f"{i}. [图片]"
        elif fav_type == 4:  # 视频
            title = item.get("title", "") or item.get("content", "") or ""
            line = f"{i}. [视频] {title}" if title else f"{i}. [视频]"
        elif fav_type == 3:  # 语音
            # Try to parse duration from content_raw
            duration_s = 0
            content_raw = item.get("content_raw", "") or ""
            import re
            dur_match = re.search(r'<duration>(\d+)</duration>', content_raw)
            if dur_match:
                duration_s = round(int(dur_match.group(1)) / 1000)
            line = f"{i}. [语音] {duration_s}秒" if duration_s else f"{i}. [语音]"
        elif fav_type == 8:  # 文件
            # Extract file name from content or title
            fname = item.get("title", "") or ""
            if not fname:
                content = item.get("content", "") or ""
                import re as _re
                fn_match = _re.search(r'<title>([^<]+)</title>', content)
                fname = fn_match.group(1) if fn_match else "文件"
            line = f"{i}. [文件] {fname}"
        elif fav_type == 33:  # 文章
            title = item.get("title", "") or item.get("content", "") or ""
            link = item.get("link", "") or ""
            if title or link:
                line = f"{i}. [文章] {title} — {link}" if link else f"{i}. [文章] {title}"

        if line:
            # Check total cap
            if total_chars + len(line) + 1 > MAX_CONTEXT_CHARS:
                lines.append(f"...(共 {len(items)} 条收藏，更多已省略)")
                break
            lines.append(line)
            total_chars += len(line) + 1

    context = "\n".join(lines)
    tokens = _estimate_tokens(context)
    return context, tokens


def _build_group_chat_context(talker: str, limit: int = 200,
                              start_time: int | None = None,
                              end_time: int | None = None) -> tuple[str, int, str]:
    """Build context text from chat messages (group or private).

    Returns (context_text, estimated_tokens, display_name).

    Safety: limits total context to MAX_CONTEXT_CHARS and per-message
    content to MAX_SINGLE_MSG_CHARS to prevent OOM on huge groups.
    Uses a smaller decompress limit to avoid blowing memory on XML blobs.

    When start_time/end_time are provided, messages are fetched in pages
    (batch_size=200, safe DLL limit) until the time range is covered.
    DLL limit>200 can crash on huge groups, so we paginate instead of
    raising the limit.
    """
    client = get_wcdb_client()
    if not client:
        return "", 0, talker

    # ── Fetch messages ──
    # If no time filter, use simple single-call (backward compatible)
    if not start_time and not end_time:
        try:
            messages = client.get_messages(talker, limit=limit)
        except Exception as e:
            logger.error("Failed to read chat messages: %s", e)
            return "", 0, talker
    else:
        # Paginated fetch: batch_size=200 (safe DLL limit), up to max_batches
        messages = []
        batch_size = 200
        max_batches = 25  # 25 × 200 = 5000 messages max
        offset = 0

        for _ in range(max_batches):
            try:
                batch = client.get_messages(talker, limit=batch_size, offset=offset)
            except Exception as e:
                logger.error("Failed to read chat messages at offset %d: %s", offset, e)
                break
            if not batch:
                break
            messages.extend(batch)
            offset += len(batch)
            # Early termination: if start_time specified and the oldest message
            # in this batch is older than start_time, we've covered the range
            if start_time and batch:
                try:
                    oldest = min(int(m.get("create_time", 0) or 0) for m in batch)
                    if oldest < start_time:
                        break
                except (ValueError, TypeError):
                    pass

        # Apply time filter
        if start_time:
            messages = [m for m in messages
                        if int(m.get("create_time", 0) or 0) >= start_time]
        if end_time:
            messages = [m for m in messages
                        if int(m.get("create_time", 0) or 0) <= end_time]

    if not messages:
        return "", 0, talker

    # Resolve display names
    sender_ids = set()
    for m in messages:
        sid = m.get("sender_username", m.get("senderUsername", m.get("sender", "")))
        if sid:
            sender_ids.add(sid)
    sender_ids.add(talker)

    name_map = client.get_display_names(list(sender_ids)) if sender_ids else {}
    display_name = name_map.get(talker, talker)

    # Format messages — progressive build with hard char cap
    lines = []
    total_chars = 0
    skipped_xml = 0

    for m in messages:
        local_type = int(m.get("local_type", m.get("localType", m.get("msg_type", 1))) or 1)
        sender = m.get("sender_username", m.get("senderUsername", m.get("sender", "")))
        sender_name = name_map.get(sender, sender)
        raw_content = m.get("message_content", "") or m.get("content", "") or ""

        # Skip obviously huge raw content early (hex-encoded compressed blobs)
        if len(raw_content) > 100_000:
            skipped_xml += 1
            display = "[长消息/已跳过]"
        else:
            # Decompress with reduced memory limit
            content = _decompress_content_safe(raw_content)

            # Strip wxid prefix for group chats
            if talker.endswith('@chatroom') and content:
                content = _strip_wxid_prefix(content)

            # Show readable text only
            if local_type == 1:  # Text
                display = content
            elif local_type == 3:
                display = "[图片]"
            elif local_type == 34:
                display = "[语音]"
            elif local_type == 43:
                display = "[视频]"
            elif local_type == 47:
                display = "[表情]"
            elif local_type == 49:
                # App message — extract title from XML
                if content and content.lstrip().startswith('<'):
                    from .api_handlers import _extract_system_msg_text
                    display = _extract_system_msg_text(content) or "[应用消息]"
                else:
                    display = content[:MAX_SINGLE_MSG_CHARS] if content else "[应用消息]"
            elif local_type == 10000:
                display = content  # System message (e.g. "xxx joined group")
            else:
                display = content[:200] if content else f"[消息类型{local_type}]"

        # Truncate single message to cap
        if len(display) > MAX_SINGLE_MSG_CHARS:
            display = display[:MAX_SINGLE_MSG_CHARS] + "..."

        if not display:
            continue

        line = f"{sender_name}: {display}"

        # Check if adding this line would exceed the total cap
        if total_chars + len(line) + 1 > MAX_CONTEXT_CHARS:
            # Add truncation notice and stop
            lines.append("...(更多消息已省略)")
            break

        lines.append(line)
        total_chars += len(line) + 1  # +1 for the newline join

    if skipped_xml:
        logger.info("Skipped %d oversized messages in context build for %s",
                     skipped_xml, talker)

    context = "\n".join(lines)
    tokens = _estimate_tokens(context)
    return context, tokens, display_name


def _decompress_content_safe(content: str) -> str:
    """Decompress zstd content with reduced memory limit for AI chat context.

    Same logic as api_handlers._decompress_content but with a much smaller
    max_output_size to prevent OOM when processing hundreds of messages.
    """
    if not content or len(content) < 16:
        return content

    # Quick check: is this hex-encoded data?
    try:
        is_hex = all(c in '0123456789abcdef' for c in content[:100].lower())
    except Exception:
        return content

    if not is_hex:
        return content

    # Check for zstd magic (28b52ffd) at the start
    if not content.lower().startswith('28b52ffd'):
        return content

    try:
        raw = bytes.fromhex(content)
        import zstandard
        dctx = zstandard.ZstdDecompressor()
        decompressed = dctx.decompress(raw, max_output_size=MAX_DECOMPRESS_SIZE)
        text = decompressed.decode('utf-8', errors='replace')
        # If >20% replacement chars, decompression likely produced garbage
        replacement_count = text.count('�')
        if len(text) > 0 and replacement_count > len(text) * 0.2:
            return content
        return text
    except zstandard.ZstdError:
        # Decompression failed (content too large for limit or corrupt)
        return "[消息解压失败]"
    except Exception:
        return content


# ── Compression ──────────────────────────────────────────────────

def _compress_session(session: AIChatSession) -> dict:
    """Compress early chat history into a summary.

    Keeps last 4 exchanges intact, compresses everything before that.
    Returns stats dict.
    """
    if len(session.chat_history) <= 8:
        return {"ok": True, "compressed_from": len(session.chat_history),
                "compressed_to": len(session.chat_history),
                "token_usage": {"used": session.estimated_tokens,
                                "budget": session.token_budget}}

    # Separate: early messages (to compress) and recent (to keep)
    early = session.chat_history[:-8]
    recent = session.chat_history[-8:]

    # Build compression prompt
    history_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content']}"
        for m in early
    )

    try:
        config = load_config()
        summarizer = create_summarizer(config)
        sys_prompt = COMPRESSION_PROMPT.format(chat_history_text=history_text)
        user_msg = "请压缩以上对话历史"

        start = time.monotonic()
        summary = summarizer._call_chat_api(
            sys_prompt,
            [{"role": "user", "content": user_msg}],
        )
        latency = (time.monotonic() - start) * 1000
        log_llm_interaction(
            backend=summarizer._backend_name,
            call_type="ai_chat_compress",
            model=summarizer.model,
            system_prompt=sys_prompt,
            user_prompt=user_msg,
            response=summary,
            latency_ms=latency,
            extra={
                "session_id": session.session_id,
                "source_type": session.source_type,
                "early_msgs": len(early),
                "recent_msgs": len(recent),
            },
        )
    except Exception as e:
        logger.error("Compression failed: %s", e)
        return {"ok": False, "error": str(e)}

    # Replace early messages with a single summary
    compressed = [{
        "role": "assistant",
        "content": f"[历史摘要] {summary}",
        "timestamp": early[0]["timestamp"],
    }]
    session.chat_history = compressed + recent
    session.estimated_tokens = session.context_tokens + _estimate_tokens(
        "\n".join(m["content"] for m in session.chat_history)
    )
    session.last_active = time.time()

    return {
        "ok": True,
        "compressed_from": len(early) + len(recent),
        "compressed_to": len(session.chat_history),
        "token_usage": {"used": session.estimated_tokens,
                        "budget": session.token_budget},
    }


# ── API handlers ──────────────────────────────────────────────────

def handle_ai_chat_start(body: dict) -> dict:
    """POST /api/ai/chat/start — Create a new AI chat session."""
    _cleanup_expired()

    source_type = body.get("source_type", "")
    source_id = body.get("source_id", "")
    # Default 200 (not 500) — some huge groups crash the WCDB DLL at 500+.
    # Users can override via the UI slider.
    message_limit = int(body.get("message_limit", 200) or 200)
    # Optional time range for group/private chat (Unix seconds)
    start_time = body.get("start_time")  # int or None
    end_time = body.get("end_time")      # int or None
    # Optional filters for favorites
    tag_id = body.get("tag_id") or None          # tag local_id string
    fav_types = body.get("fav_types") or None     # list of int, e.g. [1,14,5,33]
    tag_name = ""  # resolved later if tag_id provided

    if source_type not in ("favorites", "group_chat", "private_chat", "moments"):
        return {"ok": False, "error": "Invalid source_type"}

    if source_type == "favorites":
        context_text, context_tokens = _build_favorites_context(
            tag_id=tag_id, fav_types=fav_types
        )
        if not context_text:
            type_desc = "（仅支持文字、聊天记录、链接、文章类型）" if not fav_types else ""
            return {"ok": False, "error": f"没有可用的收藏内容{type_desc}"}
        # Build dynamic source_name
        if tag_id:
            reader = _get_wcdb_fav_reader()
            if reader:
                try:
                    all_tags = reader.get_tags() or []
                    tag_name = next((t.get("name", "") for t in all_tags
                                    if str(t.get("local_id", "")) == str(tag_id)), "")
                except Exception:
                    pass
            source_name = f"微信收藏·{tag_name}" if tag_name else "微信收藏"
        else:
            source_name = "微信收藏"
    elif source_type == "moments":
        context_text, context_tokens, source_name, sns_post_count = _build_sns_context(
            limit=message_limit, username=source_id or "",
        )
        if not context_text:
            return {"ok": False, "error": "没有可用的朋友圈内容"}
    else:
        # group_chat or private_chat — both use get_messages(talker)
        if not source_id:
            return {"ok": False, "error": "Missing source_id"}
        context_text, context_tokens, source_name = _build_group_chat_context(
            source_id, limit=message_limit, start_time=start_time, end_time=end_time
        )
        if not context_text:
            label = "群聊消息" if source_type == "group_chat" else "聊天消息"
            return {"ok": False, "error": f"没有可用的{label}"}

    # Determine token budget based on summarizer
    config = load_config()
    try:
        summarizer = create_summarizer(config)
        token_budget = summarizer.token_budget
    except Exception:
        token_budget = 100_000  # fallback

    # If context exceeds 70% of budget, pre-compress it
    if context_tokens > 0.7 * token_budget:
        logger.info("Context too large (%d tokens), pre-compressing", context_tokens)
        try:
            sys_prompt = "请将以下内容分类归纳为一段简要摘要，保留关键信息和要点："
            user_msg = context_text
            start = time.monotonic()
            summary = summarizer._call_chat_api(sys_prompt, [{"role": "user", "content": user_msg}])
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend=summarizer._backend_name,
                call_type="ai_chat_precompress",
                model=summarizer.model,
                system_prompt=sys_prompt,
                user_prompt=user_msg[:500],  # truncate to avoid bloating logs
                response=summary,
                latency_ms=latency,
                extra={
                    "source_type": source_type,
                    "source_name": source_name,
                    "original_tokens": context_tokens,
                    "budget": token_budget,
                },
            )
            context_text = f"[收藏/群聊内容摘要]\n{summary}"
            context_tokens = _estimate_tokens(context_text)
        except Exception as e:
            logger.warning("Pre-compression failed: %s, using truncated context", e)
            # Truncate to budget * 0.5 worth of chars
            max_chars = int(token_budget * 0.5 * 1.5)
            context_text = context_text[:max_chars] + "\n...(内容过长，已截断)"
            context_tokens = _estimate_tokens(context_text)

    session_id = uuid.uuid4().hex
    session = AIChatSession(
        session_id=session_id,
        source_type=source_type,
        source_id=source_id,
        source_name=source_name,
        context_text=context_text,
        context_tokens=context_tokens,
        chat_history=[],
        estimated_tokens=context_tokens,
        token_budget=token_budget,
        created_at=time.time(),
        last_active=time.time(),
    )

    with _sessions_lock:
        _sessions[session_id] = session

    op_log("AI-CHAT", "会话创建 source=%s name='%s' tokens=%d/%d",
           source_type, source_name, context_tokens, token_budget)

    # Build context summary for frontend
    if source_type == "favorites":
        # Count lines in context (each line is one favorite item)
        line_count = len([l for l in context_text.split("\n") if l and not l.startswith("...")])
        # Describe included types
        type_labels = {1: "文字", 14: "聊天记录", 5: "链接", 33: "文章",
                       2: "图片", 4: "视频", 3: "语音", 8: "文件"}
        if fav_types:
            included = "、".join(type_labels.get(t, "") for t in fav_types if t in type_labels)
        else:
            included = "文字、聊天记录、链接、文章"
        tag_suffix = f"（标签：{tag_name}）" if tag_id and tag_name else ""
        context_summary = f"已加载 {line_count} 条收藏内容（{included}）{tag_suffix}"
    elif source_type == "moments":
        context_summary = f"已加载 {sns_post_count} 条朋友圈内容"
    else:
        msg_count = len([l for l in context_text.split("\n") if l and not l.startswith("...")])
        time_desc = ""
        if start_time and end_time:
            from datetime import datetime as _dt
            s_str = _dt.fromtimestamp(start_time).strftime("%m/%d")
            e_str = _dt.fromtimestamp(end_time).strftime("%m/%d")
            time_desc = f"（{s_str}~{e_str}）"
        elif start_time:
            from datetime import datetime as _dt
            s_str = _dt.fromtimestamp(start_time).strftime("%m/%d")
            time_desc = f"（{s_str} 至今）"
        context_summary = f"已加载 {msg_count} 条{'群聊' if source_type == 'group_chat' else '聊天'}消息{time_desc}"

    return {
        "ok": True,
        "session_id": session_id,
        "source_name": source_name,
        "context_summary": context_summary,
        "token_usage": {"used": session.estimated_tokens, "budget": token_budget},
        "history": [],
    }


def handle_ai_chat_message_stream(body: dict, wfile) -> None:
    """POST /api/ai/chat/message — SSE streaming handler.

    Writes SSE events directly to wfile.
    Must NOT call send_json() — writes raw SSE format.
    """
    session_id = body.get("session_id", "")
    message = body.get("message", "").strip()

    if not session_id or not message:
        # Need to send SSE headers even for errors
        _send_sse_headers(wfile)
        _send_sse_event(wfile, "error", {"message": "Missing session_id or message"})
        return

    session = _get_session(session_id)
    if not session:
        _send_sse_headers(wfile)
        _send_sse_event(wfile, "error", {"message": "会话已过期，请重新开始"})
        return

    # Auto-compress check (>90% of budget)
    if session.estimated_tokens > 0.9 * session.token_budget:
        result = _compress_session(session)
        if not result.get("ok"):
            _send_sse_headers(wfile)
            _send_sse_event(wfile, "error", {"message": "自动压缩失败，请手动压缩或开启新会话"})
            return
        # Notify client about auto-compression
        # (will be embedded in the done event)

    # Append user message to history
    session.chat_history.append({
        "role": "user",
        "content": message,
        "timestamp": time.time(),
    })

    # Build system prompt based on source type
    if session.source_type == "favorites":
        system_prompt = FAV_CHAT_SYSTEM_PROMPT.format(
            context_text=session.context_text,
        )
    elif session.source_type == "moments":
        system_prompt = SNS_CHAT_SYSTEM_PROMPT.format(
            context_text=session.context_text,
        )
    elif session.source_type == "private_chat":
        msg_count = len(session.context_text.split("\n"))
        system_prompt = PRIVATE_CHAT_SYSTEM_PROMPT.format(
            contact_name=session.source_name,
            message_count=msg_count,
            context_text=session.context_text,
        )
    else:
        msg_count = len(session.context_text.split("\n"))
        system_prompt = GROUP_CHAT_SYSTEM_PROMPT.format(
            group_name=session.source_name,
            message_count=msg_count,
            context_text=session.context_text,
        )

    # Build messages array for API
    api_messages = [
        {"role": "user" if m["role"] == "user" else "assistant",
         "content": m["content"]}
        for m in session.chat_history
    ]

    # Get summarizer
    try:
        config = load_config()
        summarizer = create_summarizer(config)
    except Exception as e:
        _send_sse_headers(wfile)
        _send_sse_event(wfile, "error", {"message": f"AI 后端初始化失败: {e}"})
        return

    # ── Send SSE headers ────────────────────────────────────────
    _send_sse_headers(wfile)

    # ── Stream response ─────────────────────────────────────────
    FIRST_TOKEN_TIMEOUT_SEC = 40
    full_response = []
    stream_start = time.monotonic()
    first_token_received = False
    try:
        for token in summarizer._call_chat_api_stream(system_prompt, api_messages):
            if not first_token_received:
                first_token_received = True
                elapsed = time.monotonic() - stream_start
                if elapsed > FIRST_TOKEN_TIMEOUT_SEC:
                    logger.warning(f"AI chat stream: first token took {elapsed:.1f}s (> {FIRST_TOKEN_TIMEOUT_SEC}s)")
                    _send_sse_event(wfile, "error", {"message": "⚠️ AI 响应超时，请重试"})
                    try:
                        from src.web.server import _status
                        _status.update_status(ai_ok=False, ai_verified=False)
                    except Exception:
                        pass
                    return
            _send_sse_event(wfile, "token", {"content": token})
            full_response.append(token)
    except BrokenPipeError:
        logger.info("Client disconnected during AI chat stream")
        # Don't try to save — client gone
        return
    except ConnectionResetError:
        logger.info("Client reset during AI chat stream")
        return
    except Exception as e:
        logger.error("AI chat stream error: %s", e)
        try:
            _send_sse_event(wfile, "error", {"message": f"AI 服务错误: {e}"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        # Log the failed call
        latency = (time.monotonic() - stream_start) * 1000
        log_llm_interaction(
            backend=summarizer._backend_name,
            call_type="ai_chat_stream",
            model=summarizer.model,
            system_prompt=system_prompt[:500],
            user_prompt=message,
            response=f"[Error: {e}]",
            latency_ms=latency,
            extra={
                "session_id": session.session_id,
                "source_type": session.source_type,
                "source_name": session.source_name,
                "history_msgs": len(session.chat_history),
            },
        )
        return

    # Save assistant response to history
    response_text = "".join(full_response)
    session.chat_history.append({
        "role": "assistant",
        "content": response_text,
        "timestamp": time.time(),
    })
    session.estimated_tokens = session.context_tokens + _estimate_tokens(
        "\n".join(m["content"] for m in session.chat_history)
    )
    session.last_active = time.time()

    # Log the successful streaming call
    latency = (time.monotonic() - stream_start) * 1000
    log_llm_interaction(
        backend=summarizer._backend_name,
        call_type="ai_chat_stream",
        model=summarizer.model,
        system_prompt=system_prompt[:500],  # truncate to avoid bloating logs
        user_prompt=message,
        response=response_text,
        latency_ms=latency,
        extra={
            "session_id": session.session_id,
            "source_type": session.source_type,
            "source_name": session.source_name,
            "history_msgs": len(session.chat_history),
            "token_used": session.estimated_tokens,
            "token_budget": session.token_budget,
        },
    )

    # Send done event with token usage
    try:
        _send_sse_event(wfile, "done", {
            "token_usage": {"used": session.estimated_tokens,
                            "budget": session.token_budget},
            "auto_compressed": session.estimated_tokens > 0.9 * session.token_budget,
        })
    except (BrokenPipeError, ConnectionResetError):
        pass


def _send_sse_headers(wfile):
    """Send SSE response headers."""
    # Note: these are written raw to wfile — the caller must NOT
    # call send_response/send_header/end_headers first.
    # The server.py SSE handler will set these headers before
    # calling this function.
    # This function is a no-op if headers are already sent by server.py.
    pass


def handle_ai_chat_compress(body: dict) -> dict:
    """POST /api/ai/chat/compress — Manually compress conversation history."""
    session_id = body.get("session_id", "")
    if not session_id:
        return {"ok": False, "error": "Missing session_id"}

    session = _get_session(session_id)
    if not session:
        return {"ok": False, "error": "会话已过期"}

    return _compress_session(session)


def handle_ai_chat_history(params: dict) -> dict:
    """GET /api/ai/chat/history — Retrieve chat history for a session."""
    # params comes from query string: session_id=xxx
    session_id = params.get("session_id", [""])[0] if params.get("session_id") else ""
    if not session_id:
        return {"ok": False, "error": "Missing session_id"}

    session = _get_session(session_id)
    if not session:
        return {"ok": False, "error": "会话已过期"}

    return {
        "ok": True,
        "session_id": session.session_id,
        "source_name": session.source_name,
        "history": session.chat_history,
        "token_usage": {"used": session.estimated_tokens,
                        "budget": session.token_budget},
    }


def handle_ai_chat_destroy(body: dict) -> dict:
    """POST /api/ai/chat/destroy — Destroy a session."""
    session_id = body.get("session_id", "")
    if not session_id:
        return {"ok": False, "error": "Missing session_id"}

    with _sessions_lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return {"ok": True}
    return {"ok": False, "error": "Session not found"}


def handle_sns_ai_summarize_stream(body: dict, wfile) -> None:
    """POST /api/sns/ai/summarize — SSE streaming for SNS quick summary.

    Body: { limit: 20, username: "" }
    Streams: token events → done event.
    """
    logger.info(f"[SNS_SUMMARIZE] Request: limit={body.get('limit')}, username={body.get('username')}")
    limit = int(body.get("limit", 20) or 20)
    username = body.get("username", "")

    # ── TaskCenter：创建任务 ──
    tid = None
    try:
        from src.web.server import _task_center
        if _task_center:
            tid = _task_center.create_task(
                "sns_summarize", "manual", username or "朋友圈", "朋友圈总结",
            )
            _task_center.update_task(tid, progress="正在读取朋友圈内容")
    except Exception:
        pass

    # ── 辅助：错误路径清理 TaskCenter ──
    def _fail_task(error=""):
        if tid:
            try:
                from src.web.server import _task_center as _tc
                if _tc:
                    _tc.fail_task(tid, error=error[:200])
            except Exception:
                pass

    # Build context from SNS
    context_text, context_tokens, source_name, _ = _build_sns_context(
        limit=limit, username=username,
    )
    logger.info(f"[SNS_SUMMARIZE] context built: tokens={context_tokens}, source={source_name}, text_len={len(context_text) if context_text else 0}")

    if not context_text:
        _fail_task("没有朋友圈内容可总结")
        _send_sse_headers(wfile)
        _send_sse_event(wfile, "error", {"message": "没有可用的朋友圈内容"})
        return

    # Update task
    if tid:
        try:
            from src.web.server import _task_center as _tc
            if _tc:
                _tc.update_task(tid, status="running", progress="AI 生成摘要中")
        except Exception:
            pass

    # Get summarizer — need it early for token budget check
    try:
        config = load_config()
        summarizer = create_summarizer(config)
    except Exception as e:
        _fail_task(f"AI 后端初始化失败: {e}")
        _send_sse_headers(wfile)
        _send_sse_event(wfile, "error", {"message": f"AI 后端初始化失败: {e}"})
        return

    # Pre-compress context if too large (same logic as AI chat session creation)
    token_budget = summarizer.token_budget
    if context_tokens > 0.7 * token_budget:
        logger.info("[SNS_SUMMARIZE] Context too large (%d tokens), pre-compressing", context_tokens)
        sns_precompress_sys = "请将以下朋友圈内容分类归纳为一段简要摘要，保留关键信息和要点："
        try:
            sns_pre_start = time.monotonic()
            compressed = summarizer._call_chat_api(
                sns_precompress_sys,
                [{"role": "user", "content": context_text}],
            )
            sns_pre_latency = (time.monotonic() - sns_pre_start) * 1000
            log_llm_interaction(
                backend=summarizer._backend_name,
                call_type="sns_precompress",
                model=summarizer.model,
                system_prompt=sns_precompress_sys,
                user_prompt=context_text[:500],
                response=compressed,
                latency_ms=sns_pre_latency,
                extra={
                    "original_tokens": context_tokens,
                    "budget": token_budget,
                },
            )
            context_text = f"[朋友圈内容摘要]\n{compressed}"
            logger.info("[SNS_SUMMARIZE] Pre-compress done, new text_len=%d", len(context_text))
        except Exception as e:
            sns_pre_latency = (time.monotonic() - sns_pre_start) * 1000 if "sns_pre_start" in locals() else 0
            log_llm_interaction(
                backend=summarizer._backend_name,
                call_type="sns_precompress",
                model=summarizer.model,
                system_prompt=sns_precompress_sys,
                user_prompt=context_text[:500],
                response=f"[Error: {e}]",
                latency_ms=sns_pre_latency,
                extra={"original_tokens": context_tokens, "error": str(e)},
            )
            logger.warning("[SNS_SUMMARIZE] Pre-compress failed: %s", e)
            # Truncate context to budget * 0.5 worth of chars (same as AI chat)
            max_chars = int(token_budget * 0.5 * 1.5)
            context_text = context_text[:max_chars] + "\n...(内容过长，已截断)"
            context_tokens = _estimate_tokens(context_text)
            logger.info("[SNS_SUMMARIZE] Pre-compress fallback: truncated to %d chars", len(context_text))

    # Build system prompt + user message
    system_prompt = (
        "你是一个朋友圈内容分析助手。请对以下朋友圈内容做结构化总结。\n"
        "要求：\n"
        "- 用中文，按主题分类归纳\n"
        "- 挑出值得关注的动态，标注「谁：内容」，附带时间\n"
        "- 重点关注：行业动态、生活大事、新奇有趣的事\n"
        "- 忽略纯广告、无意义转发\n"
        "- 总长度控制在 500 字以内，不用每条都列"
    )
    user_message = f"请总结以下朋友圈内容：\n\n{context_text}"

    # Stream response
    FIRST_TOKEN_TIMEOUT_SEC = 40
    _send_sse_headers(wfile)
    full_response = []
    stream_start = time.monotonic()
    first_token_received = False
    try:
        for token in summarizer._call_chat_api_stream(
            system_prompt, [{"role": "user", "content": user_message}]
        ):
            if not first_token_received:
                first_token_received = True
                elapsed = time.monotonic() - stream_start
                if elapsed > FIRST_TOKEN_TIMEOUT_SEC:
                    logger.warning(f"SNS AI summarize: first token took {elapsed:.1f}s (> {FIRST_TOKEN_TIMEOUT_SEC}s)")
                    _send_sse_event(wfile, "error", {"message": "⚠️ AI 响应超时，请重试"})
                    try:
                        from src.web.server import _status
                        _status.update_status(ai_ok=False, ai_verified=False)
                    except Exception:
                        pass
                    _fail_task("AI 首 token 超时")
                    return
            _send_sse_event(wfile, "token", {"content": token})
            full_response.append(token)
    except BrokenPipeError:
        logger.info("Client disconnected during SNS AI summarize stream")
        _fail_task("客户端断开连接")
        return
    except ConnectionResetError:
        logger.info("Client reset during SNS AI summarize stream")
        _fail_task("客户端连接重置")
        return
    except Exception as e:
        logger.error("SNS AI summarize stream error: %s", e)
        _fail_task(f"AI 服务错误: {e}")
        try:
            _send_sse_event(wfile, "error", {"message": f"AI 服务错误: {e}"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        return

    # Log the call
    response_text = "".join(full_response)
    latency = (time.monotonic() - stream_start) * 1000
    log_llm_interaction(
        backend=summarizer._backend_name,
        call_type="sns_ai_summarize",
        model=summarizer.model,
        system_prompt=system_prompt[:500],
        user_prompt="请总结以上朋友圈内容",
        response=response_text,
        latency_ms=latency,
        extra={"limit": limit, "username": username},
    )

    # Task complete
    try:
        from src.web.server import _task_center as _tc
        if tid and _tc:
            _tc.complete_task(tid, result=response_text[:200])
    except Exception:
        pass

    try:
        _send_sse_event(wfile, "done", {"source_name": source_name})
    except (BrokenPipeError, ConnectionResetError):
        pass
