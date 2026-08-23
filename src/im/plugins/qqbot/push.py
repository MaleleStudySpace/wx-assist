"""QQ outbound channel backed by QQBotAdapter."""

from typing import Callable, Optional

from ...push_channel import PushChannel


class QQBotPushChannel(PushChannel):
    channel_name = "qqbot"
    platform = "qqbot"

    def __init__(self, adapter):
        self._adapter = adapter

    def is_available(self) -> bool:
        return bool(self._adapter and self._adapter.health_status().get("ok"))

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
        return {"success": ok, "error": "send failed" if not ok else ""}

    def get_status(self) -> dict:
        return self._adapter.health_status() if self._adapter else {
            "ok": False, "detail": "not initialized"
        }
