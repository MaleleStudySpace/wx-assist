"""Keyword alert engine — matches incoming messages against configured keywords."""

import logging
import time
from typing import Optional

from .config import AssistantConfig
from .outbox import Outbox

logger = logging.getLogger(__name__)

# Skip messages older than this (seconds) — prevents startup from triggering
# alerts on historical messages that the user hasn't seen yet.
ALERT_MAX_AGE_SEC = 300  # 5 minutes

# Cooldown per (group, keyword) pair — prevents alert storms when the same
# keyword is mentioned repeatedly in quick succession (e.g. a lively debate).
ALERT_COOLDOWN_SEC = 5  # 5 seconds — short cooldown to allow rapid testing


class AlertEngine:
    """Check incoming messages for keyword matches and push alerts to Outbox.

    Usage:
        engine = AlertEngine(config, outbox)
        engine.check(msg_dict)
    """

    def __init__(self, config: AssistantConfig, outbox: Outbox):
        self._config = config
        self._outbox = outbox
        # Cooldown tracker: (chat_id_or_group_name, keyword_lower) → last_trigger_ts
        self._last_triggered: dict[tuple[str, str], float] = {}

    def update_config(self, config: AssistantConfig) -> None:
        """Hot-reload config after PUT /api/assistant/config saves new config."""
        self._config = config
        # Clear cooldown tracker so new keywords take effect immediately
        self._last_triggered.clear()

    def check(self, msg: dict) -> Optional[int]:
        """Check one message against all enabled alert groups.

        Returns outbox notification ID if a keyword was matched, else None.
        """
        if not self._config.assistant_enabled:
            logger.info("Alert: assistant disabled, skipping")
            return None

        chat_id = msg.get("chat_id", "")
        group_name = msg.get("group_name", "")
        content = msg.get("content", "")
        sender_name = msg.get("sender_name", "")
        timestamp = msg.get("timestamp", 0)

        logger.info("Alert check: group_name=%r chat_id=%r content=%r", group_name, chat_id, content[:30] if content else "")

        if not group_name or not content:
            logger.info("Alert: skipping msg with empty group_name=%r or content=%r", group_name, content[:30] if content else "")
            return None

        # ── Age gate: skip old messages ──────────────────────────────
        msg_age = int(time.time()) - timestamp
        if msg_age > ALERT_MAX_AGE_SEC:
            logger.info("Alert: msg too old (age=%ds, max=%ds) from '%s'", msg_age, ALERT_MAX_AGE_SEC, group_name)
            return None

        # ── Keyword matching ─────────────────────────────────────────
        now = time.time()

        # Log all configured alert groups for debugging
        logger.info("Alert: checking %d alert groups", len(self._config.alert_groups))
        for i, ag in enumerate(self._config.alert_groups):
            logger.info("Alert group[%d]: name=%r chat_id=%r keywords=%r enabled=%s",
                i, ag.group_name, ag.chat_id, ag.keywords, ag.enabled)

        for ag in self._config.alert_groups:
            if not ag.enabled:
                logger.info("Alert: group %s disabled, skipping", ag.group_name or ag.chat_id)
                continue

            # Group identity match
            group_matched = False
            if ag.chat_id and chat_id:
                group_matched = ag.chat_id == chat_id
                logger.info("Alert: chat_id match ag.chat_id=%r vs msg.chat_id=%r => %s", ag.chat_id, chat_id, group_matched)
            elif ag.group_name:
                # Exact match (case-insensitive) — substring matching is too
                # broad and causes false positives on short group names.
                group_matched = ag.group_name.lower() == group_name.lower()
                logger.info("Alert: group_name match ag=%r vs msg=%r => %s", ag.group_name, group_name, group_matched)
            if not group_matched:
                logger.info("Alert: group not matched, skipping")
                continue
            if not ag.keywords:
                logger.info("Alert: group has no keywords, skipping")
                continue

            content_lower = content.lower()
            matched = []
            for kw in ag.keywords:
                kw_lower = kw.lower()
                if kw_lower in content_lower:
                    # ── Cooldown check ───────────────────────────────
                    cooldown_key = (chat_id or group_name, kw_lower)
                    last = self._last_triggered.get(cooldown_key, 0)
                    if now - last < ALERT_COOLDOWN_SEC:
                        logger.info(
                            "Alert cooldown: '%s' in '%s' skipped (%.0fs ago)",
                            kw, group_name, now - last,
                        )
                        continue
                    matched.append(kw)
                    self._last_triggered[cooldown_key] = now

            if matched:
                import json as _json
                title = "关键词命中"
                notif_content = _json.dumps({
                    "group": group_name,
                    "sender": sender_name,
                    "keywords": matched,
                    "message": content,
                    "display": (
                        f"[群] {group_name}\n"
                        f"[发送人] {sender_name}\n"
                        f"[关键词] {', '.join(matched)}\n"
                        f"[消息] {content}"
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
                logger.info(
                    "Alert: '%s' matched keywords %s in %s",
                    sender_name, matched, group_name,
                )

                # Push to WeChat via iLink (if configured)
                if ag.push_target == "ilink":
                    try:
                        from src.wechat.ilink_push import get_ilink_push, format_for_wechat
                        ilink = get_ilink_push()
                        if ilink.is_available():
                            push_msg = format_for_wechat(title, notif_content)
                            result = ilink.send_message(push_msg)
                            # Update push audit in outbox
                            push_ok = result.get("success", False)
                            push_err = result.get("error", "") if not push_ok else ""
                            self._outbox.update_push_result(
                                nid, "ilink",
                                "success" if push_ok else "failed",
                                push_err,
                            )
                            if push_ok:
                                logger.info("Alert pushed to WeChat for '%s'", group_name)
                            else:
                                logger.warning("WeChat push failed for '%s': %s", group_name, push_err)
                            try:
                                from src.web.api_handlers import broadcast_event
                                broadcast_event("alert_push_result", {
                                    "group_name": group_name,
                                    "success": push_ok,
                                    "error": push_err,
                                })
                            except Exception:
                                pass
                        else:
                            logger.warning("WeChat push skipped for '%s': iLink not bound", group_name)
                    except Exception as e:
                        logger.warning("WeChat push error for '%s': %s", group_name, e)
                        try:
                            self._outbox.update_push_result(nid, "ilink", "failed", str(e))
                        except Exception:
                            pass

                return nid

        return None
