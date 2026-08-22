"""Feishu outbound PushChannel."""

from typing import Callable, Optional

from ...push_channel import PushChannel


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
        try:
            self._client.send_text(
                target.split(":", 1)[1] if ":" in target else target,
                text,
            )
            self._available = True
            return {"success": True, "error": ""}
        except Exception as exc:
            self._available = False
            return {"success": False, "error": str(exc)}

    def get_status(self) -> dict:
        return {"ok": self.is_healthy(), "detail": "已配置" if self.is_available() else "未配置"}
