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
            conn.commit()

    @staticmethod
    def _channel_name(platform: str) -> str:
        return "ilink" if platform in ("wechat", "ilink") else platform

    def send_text(self, request: DeliveryRequest) -> dict:
        platform = request.platform or "wechat"
        channel_name = self._channel_name(platform)
        channel = get_plugin_push_channel(channel_name)
        attempt_id = uuid.uuid4().hex
        started = time.time()
        if channel is None:
            result = {"success": False, "error": f"IM channel unavailable: {platform}", "retryable": False}
            self._record(request, attempt_id, channel_name, started, result)
            return result
        try:
            if not channel.is_available():
                result = {"success": False, "error": f"IM channel not available: {platform}", "retryable": False}
            else:
                result = channel.send_message(
                    request.text,
                    target=request.target or None,
                )
                if not isinstance(result, dict):
                    result = {"success": bool(result), "error": "" if result else "delivery failed"}
        except Exception as exc:
            logger.warning("[delivery] %s send failed: %s", platform, exc)
            result = {"success": False, "error": str(exc), "retryable": True}
        result = dict(result)
        result["delivery_id"] = attempt_id
        result["platform"] = platform
        self._record(request, attempt_id, channel_name, started, result)
        return result

    def _record(self, request, attempt_id, channel_name, started, result):
        try:
            with self._db_lock, self._connect() as conn:
                conn.execute("""
                    INSERT INTO im_delivery_attempts
                    (id, platform, channel, target, source_type, source_id,
                     inbound_message_id, conversation_key, status, attempt_no,
                     provider_message_id, provider_code, provider_error,
                     retryable, created_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """, (
                    attempt_id, request.platform, channel_name, request.target,
                    request.source_type, request.source_id, request.inbound_message_id,
                    request.conversation_key, "success" if result.get("success") else "failed",
                    str(result.get("message_id", "")), str(result.get("code", "")),
                    str(result.get("error", ""))[:1000], int(bool(result.get("retryable"))),
                    started, time.time(),
                ))
                conn.commit()
        except Exception as exc:
            # Delivery auditing must never break the actual notification path.
            logger.warning("[delivery] audit write failed: %s", exc)

    def list_attempts(self, platform: str = "", limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM im_delivery_attempts"
        args = []
        if platform:
            sql += " WHERE platform = ?"
            args.append(platform)
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
