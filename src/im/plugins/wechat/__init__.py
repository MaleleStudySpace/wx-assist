"""Optional WeChat adapter wrapper.

The existing src.wechat backends remain the source of truth until the
migration segment explicitly connects this wrapper to Bot.
"""

from .adapter import WechatAdapter
from .push import WechatPushChannel, get_wechat_push_channel, reset_wechat_push_channel

__all__ = [
    "WechatAdapter",
    "WechatPushChannel",
    "get_wechat_push_channel",
    "reset_wechat_push_channel",
]
