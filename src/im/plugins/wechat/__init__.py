"""WeChat iLink PushChannel compatibility exports.

The legacy WeChat runtime remains outside the optional adapter registry.  The
unfinished ``WechatAdapter`` placeholder is intentionally not auto-registered.
"""

from .adapter import WechatAdapter
from .push import WechatPushChannel, get_wechat_push_channel, reset_wechat_push_channel

__all__ = [
    "WechatAdapter",
    "WechatPushChannel",
    "get_wechat_push_channel",
    "reset_wechat_push_channel",
]
