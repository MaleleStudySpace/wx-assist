"""Optional WeChat adapter wrapper.

The existing src.wechat backends remain the source of truth until the
migration segment explicitly connects this wrapper to Bot.
"""

from .adapter import WechatAdapter

__all__ = ["WechatAdapter"]
