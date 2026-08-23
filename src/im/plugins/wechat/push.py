"""WeChat iLink implementation of the IM PushChannel contract.

The underlying iLink API, account storage, QR flow, rate limiting and retry
behavior remain in ``src.wechat.ilink_push``.  This wrapper only translates
between the generic channel contract and the existing implementation.
"""

from typing import Callable, Optional

from ...push_channel import PushChannel


class WechatPushChannel(PushChannel):
    channel_name = "ilink"  # Keep the historical outbox value unchanged.
    platform = "wechat"

    def __init__(self, implementation=None):
        self._implementation = implementation

    def _get_implementation(self):
        if self._implementation is not None:
            return self._implementation
        # 不缓存底层单例：web API 的 bind/unbind 会重置它。实时取新实例，
        # 否则扫码重绑后 wrapper 仍引用旧账号，推送误报"未绑定"。
        from src.wechat.ilink_push import get_ilink_push
        return get_ilink_push()

    def is_available(self) -> bool:
        return bool(self._get_implementation().is_available())

    def is_healthy(self) -> bool:
        return bool(self._get_implementation().is_healthy())

    def send_message(self, text: str, *,
                     progress_callback: Optional[Callable] = None,
                     target: Optional[str] = None) -> dict:
        # iLink is a single-account WeChat channel; target is intentionally
        # ignored so this wrapper cannot accidentally route to another IM.
        return self._get_implementation().send_message(
            text,
            progress_callback=progress_callback,
        )

    def get_status(self) -> dict:
        return dict(self._get_implementation().get_status())

    @staticmethod
    def format_message(title: str, content: str) -> str:
        from src.wechat.ilink_push import format_for_wechat
        return format_for_wechat(title, content)


_channel: Optional[WechatPushChannel] = None


def get_wechat_push_channel() -> WechatPushChannel:
    global _channel
    if _channel is None:
        _channel = WechatPushChannel()
    return _channel


def reset_wechat_push_channel() -> None:
    global _channel
    _channel = None
