"""Unified IM delivery service and per-attempt delivery audit.

Business modules submit a platform-neutral request here instead of importing a
provider SDK. Legacy WeChat/iLink behavior remains the default mapping.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .plugins import get_plugin_push_channel
from .targets import bound_push_targets, default_target, normalize_targets
from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)
DB_PATH = PROJECT_ROOT / "data" / "im_delivery.db"


@dataclass(frozen=True)
class DeliveryRequest:
    platform: str
    text: str
    target: str = ""
    source_type: str = ""
    source_id: str = ""
    inbound_message_id: str = ""
    conversation_key: str = ""
    reply_to: str = ""
    # Stable notification linkage used by task-center retries.  These fields
    # stay optional so agent replies and legacy callers remain unchanged.
    notification_id: str = ""
    outbox_id: int = 0
    task_id: int = 0
    # None keeps the source-type compatibility rule; False is used by retry
    # to preserve its historical failed-channel selection.
    auto_route: Optional[bool] = None


def aggregate_status(results: list[dict]) -> str:
    """Collapse per-channel results into one push status.

    ``partial`` means at least one channel delivered and at least one did not.
    Callers treat it as terminal — it is deliberately excluded from task-center
    re-push so channels that already delivered are never sent a duplicate.
    """
    delivered = sum(1 for item in results if item.get("success", False))
    if delivered == len(results) and delivered > 0:
        return "success"
    if delivered > 0:
        return "partial"
    return "failed"


class DeliveryService:
    """Serialize delivery auditing while delegating protocol work to channels."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._db_lock = threading.RLock()
        self._init_db()

    def _connect(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._db_lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS im_delivery_attempts (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL DEFAULT '',
                    inbound_message_id TEXT NOT NULL DEFAULT '',
                    conversation_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    provider_message_id TEXT NOT NULL DEFAULT '',
                    provider_code TEXT NOT NULL DEFAULT '',
                    provider_error TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    finished_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_im_delivery_created ON im_delivery_attempts(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_im_delivery_platform ON im_delivery_attempts(platform, created_at DESC)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(im_delivery_attempts)").fetchall()}
            for name, definition in (
                ("notification_id", "TEXT NOT NULL DEFAULT ''"),
                ("outbox_id", "INTEGER NOT NULL DEFAULT 0"),
                ("task_id", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE im_delivery_attempts ADD COLUMN {name} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_im_delivery_source ON im_delivery_attempts(source_type, source_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_im_delivery_outbox ON im_delivery_attempts(outbox_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_im_delivery_task ON im_delivery_attempts(task_id, created_at DESC)")
            conn.commit()

    @staticmethod
    def _channel_name(platform: str) -> str:
        return "ilink" if platform in ("wechat", "ilink") else platform

    def send_bound_text(self, request: DeliveryRequest) -> dict:
        """Deliver to every currently QR-bound channel."""
        platforms = bound_push_targets()
        if not platforms:
            return {"success": True, "skipped": True, "status": "skipped",
                    "error": "no bound delivery platform"}
        results = []
        for platform in platforms:
            target = default_target(platform) if platform != "ilink" else ""
            results.append(self._send_one(request, platform, target))
        success = all(item.get("success", False) for item in results)
        return {"success": success, "status": aggregate_status(results), "results": results,
                "error": "" if success else "; ".join(item.get("error", "") for item in results if item.get("error"))}

    def send_text(self, request: DeliveryRequest) -> dict:
        if request.auto_route is True:
            return self.send_bound_text(request)
        platforms = normalize_targets(request.platform)
        if not platforms:
            return {"success": True, "skipped": True, "status": "skipped",
                    "error": "no delivery platform"}
        results = []
        for platform in platforms:
            target = request.target or default_target(platform)
            result = self._send_one(request, platform, target)
            results.append(result)
        success = all(item.get("success", False) for item in results)
        return {"success": success, "status": aggregate_status(results), "results": results,
                "error": "" if success else "; ".join(item.get("error", "") for item in results if item.get("error"))}

    def _send_one(self, request: DeliveryRequest, platform: str, target: str) -> dict:
        platform_name = platform
        channel_name = self._channel_name(platform_name)
        channel = get_plugin_push_channel(channel_name)
        attempt_id = uuid.uuid4().hex
        started = time.time()
        # Fallback: if channel not yet registered but credentials exist,
        # construct one from PlatformRegistry adapter on the fly.
        if channel is None:
            try:
                from .registry import get_global_registry
                adapter = get_global_registry().get_adapter(channel_name) if get_global_registry() else None
                if adapter and getattr(adapter, "_client", None) and adapter._client.app_id:
                    if channel_name == "qqbot":
                        from .plugins.qqbot.push import QQBotPushChannel
                        channel = QQBotPushChannel(adapter)
                    elif channel_name == "feishu":
                        from .plugins.feishu.push import FeishuPushChannel
                        channel = FeishuPushChannel(adapter._client)
                    elif channel_name == "ilink":
                        from .plugins.wechat.push import WechatPushChannel
                        channel = WechatPushChannel()
            except Exception:
                pass
        if channel is None:
            result = {"success": False, "error": f"IM channel unavailable: {platform_name}", "retryable": False}
            self._record(request, attempt_id, channel_name, target, started, result)
            return result
        try:
            if not channel.is_available():
                result = {"success": False, "error": f"IM channel not available: {platform_name}", "retryable": False}
            else:
                result = channel.send_message(
                    request.text,
                    target=target or None,
                )
                if not isinstance(result, dict):
                    result = {"success": bool(result), "error": "" if result else "delivery failed"}
        except Exception as exc:
            logger.warning("[delivery] %s send failed: %s", platform, exc)
            result = {"success": False, "error": str(exc), "retryable": True}
        result = dict(result)
        result["delivery_id"] = attempt_id
        result["platform"] = platform
        self._record(request, attempt_id, channel_name, target, started, result)
        return result

    @staticmethod
    def format_text(platform: str, title: str, content: str) -> str:
        """Format notification text with the shared product representation.

        The existing WeChat/iLink formatting is also the intended format for
        QQ and Feishu.  Keep ``platform`` in the signature for compatibility,
        but do not let the first bound channel choose a different format.
        """
        from src.wechat.ilink_push import format_for_wechat
        return format_for_wechat(title, content)

    @staticmethod
    def outbox_channel(result: dict, fallback_platforms=()) -> str:
        """Choose a truthful channel label for a legacy Outbox row.

        Delivery attempts are the per-platform source of truth.  The legacy
        Outbox has one channel column, so it is populated only when exactly
        one actual channel represents the result.  Multi-channel deliveries
        leave it empty and remain visible through their individual attempts;
        a skipped result has no channel because no platform was contacted.
        """
        result = result or {}
        if result.get("skipped"):
            return ""
        fallback = normalize_targets(fallback_platforms)
        rows = result.get("results") or []
        platforms = []
        failed = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            platform = DeliveryService._channel_name(
                str(row.get("platform") or row.get("channel") or "").strip()
            )
            if platform not in {"ilink", "qqbot", "feishu"}:
                continue
            if platform not in platforms:
                platforms.append(platform)
            if not row.get("success", False) and platform not in failed:
                failed.append(platform)
        selected = failed if not result.get("success", False) and failed else platforms
        if not selected:
            selected = fallback
        # One legacy channel field cannot faithfully represent a multi-channel
        # delivery.  Leave it empty and let im_delivery_attempts expose the
        # per-platform records instead of inventing a channel name.
        return selected[0] if len(selected) == 1 else ""

    def _record(self, request, attempt_id, channel_name, target, started, result):
        # The registered WeChat channel is ilink; use the resolved channel name
        # in audit rows so retry can select the exact failed channel.
        record_platform = channel_name
        try:
            with self._db_lock, self._connect() as conn:
                record_platform = channel_name
                row = conn.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) FROM im_delivery_attempts "
                    "WHERE outbox_id=? AND platform=? AND target=?",
                    (int(request.outbox_id or 0), record_platform, target),
                ).fetchone()
                attempt_no = int(row[0] or 0) + 1
                conn.execute("""
                    INSERT INTO im_delivery_attempts
                    (id, platform, channel, target, source_type, source_id,
                     inbound_message_id, conversation_key, status, attempt_no,
                     provider_message_id, provider_code, provider_error,
                     retryable, created_at, finished_at, notification_id,
                     outbox_id, task_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    attempt_id, record_platform, channel_name, target,
                    request.source_type, request.source_id, request.inbound_message_id,
                    request.conversation_key, "success" if result.get("success") else "failed",
                    attempt_no, str(result.get("message_id", "")), str(result.get("code", "")),
                    str(result.get("error", ""))[:1000], int(bool(result.get("retryable"))),
                    started, time.time(), request.notification_id,
                    int(request.outbox_id or 0), int(request.task_id or 0),
                ))
                conn.commit()
        except Exception as exc:
            # Delivery auditing must never break the actual notification path.
            logger.warning("[delivery] audit write failed: %s", exc)

    def list_recent_failures(self, channel: str, limit: int = 1) -> list[dict]:
        """Return the latest failed attempt unless a newer success cleared it."""
        rows = self.list_attempts(platform=channel, limit=max(2, limit * 3))
        if not rows:
            return []
        if rows[0].get("status") == "success":
            return []
        return [row for row in rows if row.get("status") == "failed"][:limit]

    def list_attempts(self, platform: str = "", limit: int = 50,
                      *, source_type: str = "", source_id: str = "",
                      outbox_id: int = 0, task_id: int = 0, status: str = "",
                      exclude_source_types=()) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM im_delivery_attempts"
        args = []
        clauses = []
        if platform:
            clauses.append("platform = ?")
            args.append(platform)
        if source_type:
            clauses.append("source_type = ?")
            args.append(source_type)
        if status:
            clauses.append("status = ?")
            args.append(status)
        excluded = tuple(str(t).strip() for t in (exclude_source_types or ()) if str(t).strip())
        if excluded:
            # 在 SQL 层排除，LIMIT 才作用于排除之后的结果集；调用方取回后再过滤
            # 会让每页条数不足，分页与计数全部失真。
            clauses.append("source_type NOT IN (%s)" % ",".join("?" * len(excluded)))
            args.extend(excluded)
        if source_id:
            clauses.append("source_id = ?")
            args.append(str(source_id))
        if outbox_id:
            clauses.append("outbox_id = ?")
            args.append(int(outbox_id))
        if task_id:
            clauses.append("task_id = ?")
            args.append(int(task_id))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._db_lock, self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]


_service: Optional[DeliveryService] = None
_service_lock = threading.Lock()


def get_delivery_service() -> DeliveryService:
    global _service
    with _service_lock:
        if _service is None:
            _service = DeliveryService()
        return _service


def reset_delivery_service() -> None:
    global _service
    with _service_lock:
        _service = None
