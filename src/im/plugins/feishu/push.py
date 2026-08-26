"""Feishu outbound PushChannel."""

import logging
from typing import Callable, Optional

from ...push_channel import PushChannel

logger = logging.getLogger(__name__)


class FeishuPushChannel(PushChannel):
    channel_name = "feishu"
    platform = "feishu"

    def __init__(self, client):
        self._client = client
        self._available = False

    def is_available(self) -> bool:
        return bool(self._client and self._client.app_id and self._client.app_secret)

    def is_healthy(self) -> bool:
        return self._available or self.is_available()

    def send_message(self, text: str, *,
                     progress_callback: Optional[Callable] = None,
                     target: Optional[str] = None) -> dict:
        if not target:
            return {"success": False, "error": "Feishu target chat_id is required"}
        target_value = target
        if target_value.startswith("feishu:"):
            target_value = target_value.split(":", 1)[1]
        receive_id_type = "chat_id"
        if target_value.startswith("open_id:"):
            receive_id_type = "open_id"
            native_id = target_value.split(":", 1)[1]
        else:
            native_id = target_value
        try:
            logger.info("[feishu] send_text -> target=%s type=%s len=%d", native_id, receive_id_type, len(text))
            resp = self._client.send_text(native_id, text, receive_id_type=receive_id_type)
            logger.info("[feishu] send_text OK: %s", str(resp)[:200])
            self._available = True
            return {
                "success": True,
                "error": "",
                "message_id": str(resp.get("data", {}).get("message_id", "")),
                "request_id": str(resp.get("request_id", "")),
                "provider_response": resp,
            }
        except Exception as exc:
            logger.warning("[feishu] send_text failed: %s", exc)
            self._available = False
            return {"success": False, "error": str(exc)}

    def get_status(self) -> dict:
        return {"ok": self.is_healthy(), "detail": "已配置" if self.is_available() else "未配置"}
