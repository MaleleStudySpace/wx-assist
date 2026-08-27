"""QQ outbound channel backed by QQBotAdapter."""

from typing import Callable, Optional

from ...push_channel import PushChannel


class QQBotPushChannel(PushChannel):
    channel_name = "qqbot"
    platform = "qqbot"

    def __init__(self, adapter):
        self._adapter = adapter

    def is_available(self) -> bool:
        """Check whether QQ OpenAPI sending can be attempted.

        Outbound delivery uses QQ OpenAPI and does not require the inbound
        Gateway WebSocket to have reached READY.  Gateway health is still
        exposed separately by the adapter for inbound-message diagnostics.
        """
        return bool(
            self._adapter
            and self._adapter.is_configured()
        )

    def is_healthy(self) -> bool:
        return self.is_available()

    def send_message(self, text: str, *,
                     progress_callback: Optional[Callable] = None,
                     target: Optional[str] = None) -> dict:
        if not self._adapter or not target:
            return {"success": False, "error": "QQ target is required"}
        # 保留 qqbot: 命名空间 — adapter 用它查 chat type 后再拆出 native id。
        # 若提前剥掉前缀，群聊会丢失 group 类型映射，误发到 /v2/users 接口。
        ok = self._adapter.send_text(target, text)
        response = getattr(self._adapter, "_last_provider_response", {}) or {}
        return {
            "success": ok,
            "error": "send failed" if not ok else "",
            "message_id": str(response.get("id", response.get("message_id", ""))),
            "provider_response": response,
        }

    def get_status(self) -> dict:
        return self._adapter.health_status() if self._adapter else {
            "ok": False, "detail": "not initialized"
        }
