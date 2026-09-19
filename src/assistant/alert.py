"""Keyword alert engine — matches incoming messages against configured keywords.

Persistent dedup: triggered message_ids are saved to data/alert_triggered.json
so the same message never fires an alert twice, even after restart or
_poll_group error recovery that clears the in-memory _known_ids set.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from .config import (
    AssistantConfig,
    is_regex_keyword,
    regex_keyword_pattern,
    validate_alert_keywords,
)
from .digest import _strip_ids
from .outbox import Outbox

logger = logging.getLogger(__name__)

# 正则命中片段在推送正文里的展示长度上限。
_HIT_SNIPPET_MAX = 30

# Skip messages older than this (seconds) — prevents startup from triggering
# alerts on historical messages that the user hasn't seen yet.
ALERT_MAX_AGE_SEC = 300  # 5 minutes

# Cooldown per (group, keyword) pair — prevents alert storms when the same
# keyword is mentioned repeatedly in quick succession (e.g. a lively debate).
ALERT_COOLDOWN_SEC = 5  # 5 seconds

# ── Persistent dedup ───────────────────────────────────────────────────
_TRIGGERED_PATH = Path("data/alert_triggered.json")
_MAX_TRIGGERED_RECORDS = 5000
_TRIGGERED_CLEANUP_AGE = 86400 * 7  # 7 days — keep at most a week of history


def _clip_hit(text: str) -> str:
    """截断正则命中片段，避免超长内容把推送正文撑爆。"""
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= _HIT_SNIPPET_MAX:
        return text
    return text[:_HIT_SNIPPET_MAX] + "…"


def _load_triggered() -> dict[str, float]:
    """Load message_id → timestamp map from disk."""
    try:
        data = json.loads(_TRIGGERED_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _save_triggered(data: dict[str, float]) -> None:
    """Atomically save message_id → timestamp map to disk."""
    try:
        _TRIGGERED_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _TRIGGERED_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, _TRIGGERED_PATH)
    except OSError as e:
        logger.warning("Failed to save alert_triggered.json: %s", e)


def _cleanup_triggered(data: dict[str, float]) -> dict[str, float]:
    """Remove entries older than _TRIGGERED_CLEANUP_AGE and trim to max size."""
    now = time.time()
    cutoff = now - _TRIGGERED_CLEANUP_AGE
    kept = {k: v for k, v in data.items() if v >= cutoff}
    if len(kept) > _MAX_TRIGGERED_RECORDS:
        # Keep the most recent N
        sorted_items = sorted(kept.items(), key=lambda x: x[1], reverse=True)
        kept = dict(sorted_items[:_MAX_TRIGGERED_RECORDS])
    return kept


class AlertEngine:
    """Check incoming messages for keyword matches and push alerts to Outbox.

    Uses both in-memory cooldown (per-group per-keyword, ALERT_COOLDOWN_SEC)
    and persistent message_id dedup (via alert_triggered.json) so the same
    message never fires twice even after restart or poll-cycle error recovery.
    """

    def __init__(self, config: AssistantConfig, outbox: Outbox):
        self._config = config
        self._outbox = outbox
        # Cooldown tracker: (chat_id_or_group_name, keyword_key) → last_trigger_ts
        self._last_triggered: dict[tuple[str, str], float] = {}
        # Persistent dedup: message_id → timestamp  (survives restarts)
        self._triggered: dict[str, float] = _load_triggered()
        self._triggered_dirty = False
        # Pre-compiled regex keywords: 关键词原文(/pattern/) → compiled Pattern
        self._compiled: dict[str, re.Pattern] = {}
        self._rebuild_compiled()

    def _rebuild_compiled(self) -> None:
        """预编译配置里的正则关键词。

        check() 跑在**每条消息的接收线程**上，绝不能在那里 re.compile；
        配置变更时重建一次即可（构造 + update_config）。

        编译失败的条目只记 warning 后跳过，而不是让整组关键词失效：
        用户手改 assistant_config.json 塞进坏正则时，其余关键词仍要正常工作，
        也不能变成每条消息刷一条异常。
        """
        compiled: dict[str, re.Pattern] = {}
        for ag in self._config.alert_groups:
            for kw in ag.keywords or []:
                if kw in compiled or not is_regex_keyword(kw):
                    continue
                # 复用配置层的校验规则（非空 / 长度上限 / 可编译），
                # 保证「保存被接受」与「运行期可执行」用的是同一套判据。
                err = validate_alert_keywords([kw])
                if err:
                    logger.warning(
                        "Alert: 跳过不可用的正则关键词（group=%s）: %s",
                        ag.group_name or ag.chat_id, err,
                    )
                    continue
                compiled[kw] = re.compile(regex_keyword_pattern(kw))
        self._compiled = compiled

    def update_config(self, config: AssistantConfig) -> None:
        """Hot-reload config after PUT /api/assistant/config saves new config."""
        self._config = config
        # Clear cooldown tracker so new keywords take effect immediately
        self._last_triggered.clear()
        # Recompile regex keywords (new/removed patterns take effect immediately)
        self._rebuild_compiled()
        # Persist triggered set if it was dirtied
        self._flush_triggered()

    def _flush_triggered(self) -> None:
        """Write triggered set to disk if dirty."""
        if self._triggered_dirty:
            _save_triggered(self._triggered)
            self._triggered_dirty = False

    def _record_triggered(self, message_id: str) -> None:
        """Record a message_id as already triggered and persist."""
        self._triggered[message_id] = time.time()
        self._triggered_dirty = True
        # Periodically trim
        if len(self._triggered) > _MAX_TRIGGERED_RECORDS:
            self._triggered = _cleanup_triggered(self._triggered)

    def check(self, msg: dict) -> Optional[int]:
        """Check one message against all enabled alert groups.

        Returns outbox notification ID if a keyword was matched, else None.
        """
        if not self._config.assistant_enabled:
            return None

        chat_id = msg.get("chat_id", "")
        group_name = msg.get("group_name", "") or chat_id
        content = msg.get("content", "")
        # Strip raw wxid/gh_ identifiers before keyword matching and display
        content = _strip_ids(content)
        sender_name = msg.get("sender_name", "")
        timestamp = msg.get("timestamp", 0)
        message_id = msg.get("message_id", "")

        logger.debug("Alert check: group_name=%r chat_id=%r content=%r", group_name, chat_id, content[:30] if content else "")

        if not group_name or not content:
            logger.debug("Alert: skipping msg with empty group_name=%r or content=%r", group_name, content[:30] if content else "")
            return None

        # ── Age gate: skip old messages ──────────────────────────────
        msg_age = int(time.time()) - timestamp
        if msg_age > ALERT_MAX_AGE_SEC:
            logger.debug("Alert: msg too old (age=%ds, max=%ds) from '%s'", msg_age, ALERT_MAX_AGE_SEC, group_name)
            return None

        # ── Persistent dedup: skip already-triggered messages ─────────
        if message_id and message_id in self._triggered:
            logger.debug("Alert: msg %s already triggered, skipping", message_id[:12])
            return None

        # ── Keyword matching ─────────────────────────────────────────
        now = time.time()

        logger.debug("Alert: checking %d groups for %s", len(self._config.alert_groups), group_name)
        for ag in self._config.alert_groups:
            if not ag.enabled:
                continue

            # Group identity match
            group_matched = False
            if ag.chat_id and chat_id:
                group_matched = ag.chat_id == chat_id
            elif ag.group_name:
                group_matched = ag.group_name.lower() == group_name.lower()
            if not group_matched:
                continue
            if not ag.keywords:
                continue

            content_lower = content.lower()
            matched = []
            # 正则条目 → 实际命中片段，仅用于推送正文展示
            hits: dict[str, str] = {}
            for kw in ag.keywords:
                if not isinstance(kw, str) or not kw:
                    continue
                pattern = self._compiled.get(kw)
                if pattern is not None:
                    # 正则默认大小写敏感：必须匹配**原始** content。
                    # 若匹配 content.lower()，[A-Z] / \b 这类语义会失效。
                    m = pattern.search(content)
                    if not m:
                        continue
                    hit = _clip_hit(m.group(0))
                    if hit:
                        hits[kw] = hit
                    kw_key = kw
                else:
                    # 字面关键词：既有行为，大小写不敏感的子串包含
                    if kw.lower() not in content_lower:
                        continue
                    kw_key = kw.lower()
                # ── Cooldown check ───────────────────────────────
                cooldown_key = (chat_id or group_name, kw_key)
                last = self._last_triggered.get(cooldown_key, 0)
                if now - last < ALERT_COOLDOWN_SEC:
                    logger.debug(
                        "Alert cooldown: '%s' in '%s' skipped (%.0fs ago)",
                        kw, group_name, now - last,
                    )
                    continue
                matched.append(kw)
                self._last_triggered[cooldown_key] = now

            if matched:
                import json as _json
                # 正则条目额外展示实际命中片段：只给用户看一个 pattern，
                # 很难判断为什么触发；`/\d{2,}元/` ‹500元› 一目了然。
                # keywords 字段保持纯字符串列表，下游 UI 无需改动。
                tag_strs = [
                    f"`{kw}`" if kw not in hits else f"`{kw}` ‹{hits[kw]}›"
                    for kw in matched
                ]
                title = f"🔑 关键词命中 · {group_name}"
                notif_content = _json.dumps({
                    "group": group_name,
                    "sender": sender_name,
                    "keywords": matched,
                    "message": content,
                    "display": (
                        f"👤 **发送者:** {sender_name}\n"
                        f"💬 **消息:** {content}\n"
                        + "🏷️ **匹配关键词:** " + " ".join(tag_strs)
                    ),
                }, ensure_ascii=False)
                nid = self._outbox.add(
                    notif_type="keyword_alert",
                    chat_id=chat_id,
                    group_name=group_name,
                    title=title,
                    content=notif_content,
                    priority="high",
                )
                # Persistent dedup: record this message_id so it never
                # triggers again, even after restart or _reinitialize()
                if nid and message_id:
                    self._record_triggered(message_id)
                    self._flush_triggered()

                logger.info(
                    "Alert: '%s' matched keywords %s in %s",
                    sender_name, matched, group_name,
                )

                # Push to all QR-bound IM channels.  The legacy target is kept
                # only for compatibility; DeliveryService resolves bindings.
                try:
                    import json as _json
                    push_data = _json.loads(notif_content) if isinstance(notif_content, str) else notif_content
                    push_text = push_data.get("display", notif_content)
                    from src.im.delivery import DeliveryRequest, DeliveryService, get_delivery_service
                    from src.im.targets import bound_push_targets
                    bound = bound_push_targets()
                    if not bound:
                        self._outbox.update_push_result(nid, "", "skipped", "未绑定任何推送渠道")
                        return nid
                    fmt_target = bound[0]
                    push_msg = DeliveryService.format_text("", title, push_text)
                    result = get_delivery_service().send_text(DeliveryRequest(
                        platform=fmt_target,
                        text=push_msg,
                        target="",
                        source_type="keyword_alert",
                        source_id=str(nid),
                        outbox_id=nid or 0,
                        inbound_message_id=message_id,
                        conversation_key=chat_id,
                        auto_route=True,
                    ))
                    push_ok = result.get("success", False)
                    push_status = result.get("status") or ("success" if push_ok else "failed")
                    push_err = "" if push_status == "success" else result.get("error", "")
                    self._outbox.update_push_result(
                        nid, DeliveryService.outbox_channel(result, bound),
                        push_status,
                        push_err,
                    )
                    if push_ok:
                        logger.info("Alert pushed through bound IM channels for '%s'", group_name)
                    else:
                        logger.warning("IM push failed for '%s': %s", group_name, push_err)
                    try:
                        from src.web.api_handlers import broadcast_event
                        broadcast_event("alert_push_result", {
                            "group_name": group_name,
                            "success": push_ok,
                            "error": push_err,
                        })
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("IM push error for '%s': %s", group_name, e)
                    try:
                        fallback_channel = DeliveryService.outbox_channel(
                            {"success": False, "error": str(e)}, bound_push_targets()
                        )
                        self._outbox.update_push_result(nid, fallback_channel, "failed", str(e))
                    except Exception:
                        pass

                return nid

        return None
