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
from .targets import default_target, normalize_targets

logger = logging.getLogger(__name__)
DB_PATH = Path("data/im_delivery.db")


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

    def send_text(self, request: DeliveryRequest) -> dict:
        platforms = normalize_targets(request.platform)
        if not platforms:
            return {"success": True, "skipped": True, "error": "no delivery platform"}
        results = []
        for platform in platforms:
            target = request.target or default_target(platform)
            result = self._send_one(request, platform, target)
            results.append(result)
        success = all(item.get("success", False) for item in results)
        return {"success": success, "results": results,
                "error": "" if success else "; ".join(item.get("error", "") for item in results if item.get("error"))}

    def _send_one(self, request: DeliveryRequest, platform: str, target: str) -> dict:
        channel_name = self._channel_name(platform)
        channel = get_plugin_push_channel(channel_name)
        attempt_id = uuid.uuid4().hex
        started = time.time()
        if channel is None:
            result = {"success": False, "error": f"IM channel unavailable: {platform}", "retryable": False}
            self._record(request, attempt_id, channel_name, target, started, result)
            return result
        try:
            if not channel.is_available():
                result = {"success": False, "error": f"IM channel not available: {platform}", "retryable": False}
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
        """Format title/body through one channel when possible.

        For multi-target delivery, use a neutral representation so one target's
        provider-specific formatter cannot corrupt another target's message.
        """
        targets = normalize_targets(platform)
        if len(targets) == 1:
            channel = get_plugin_push_channel(DeliveryService._channel_name(targets[0]))
            if channel is not None:
                return channel.format_message(title, content)
        return f"{title}\n\n{content}" if title and content else (title or content)

    def _record(self, request, attempt_id, channel_name, target, started, result):
        # The registered WeChat channel is ilink; use the resolved channel name
        # in audit rows so retry can select the exact failed channel.
        record_platform = channel_name
        try:
            with self._db_lock, self._connect() as conn:
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
                    1, str(result.get("message_id", "")), str(result.get("code", "")),
                    str(result.get("error", ""))[:1000], int(bool(result.get("retryable"))),
                    started, time.time(), request.notification_id,
                    int(request.outbox_id or 0), int(request.task_id or 0),
                ))
                conn.commit()
        except Exception as exc:
            # Delivery auditing must never break the actual notification path.
            logger.warning("[delivery] audit write failed: %s", exc)

    def list_attempts(self, platform: str = "", limit: int = 50,
                      *, source_type: str = "", source_id: str = "",
                      outbox_id: int = 0, task_id: int = 0) -> list[dict]:
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
