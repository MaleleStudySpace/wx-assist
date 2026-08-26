"""Non-invasive wrapper placeholder for the existing WeChat backends."""

from typing import Optional

from ...base import BasePlatformAdapter, MessageCallback
from ...config_schema import PlatformConfig
from ...message import SessionSource
from ...plugins import register


@register("wechat")
class WechatAdapter(BasePlatformAdapter):
    """Adapter boundary kept separate until the migration segment is accepted."""

    platform_name = "wechat"
    display_name = "微信"
    supports_image = True
    supports_voice = True
    supports_file = True
    text_message_max_len = 4000

    def __init__(self, config: PlatformConfig):
        self.config = config
        self._backend = None

    def start(self, callback: MessageCallback,
              groups: list[str] | None = None) -> bool:
        raise NotImplementedError(
            "WechatAdapter is not connected to the legacy Bot path yet"
        )

    def stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()

    def send_text(self, chat_id: str, content: str,
                  *, reply_to: Optional[str] = None) -> bool:
        raise NotImplementedError(
            "WechatAdapter is not connected to the legacy Bot path yet"
        )

    def health_status(self) -> dict:
        return {"ok": False, "detail": "adapter migration not enabled"}

    def list_chats(self) -> list[SessionSource]:
        return []
