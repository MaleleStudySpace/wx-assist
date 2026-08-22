"""Route Agent progress to the platform that initiated the conversation."""

import logging
from typing import Optional

from .push_channel import PushChannel

logger = logging.getLogger(__name__)


class ProgressRouter:
    """Small in-process registry for non-blocking platform progress delivery."""

    def __init__(self) -> None:
        self._channels: dict[str, PushChannel] = {}

    def register(self, platform: str, channel: PushChannel) -> None:
        name = str(platform or "").strip()
        if not name:
            raise ValueError("platform is required")
        self._channels[name] = channel

    def unregister(self, platform: str) -> None:
        self._channels.pop(platform, None)

    def push(self, platform: Optional[str], text: str,
             target: Optional[str] = None) -> bool:
        """Send progress if a usable channel exists; never raise to Agent.

        ``target`` is required by multi-account or per-conversation channels
        such as QQ.  WeChat iLink intentionally ignores it because it is a
        single bound account.
        """
        if not platform or not text:
            return False
        channel = self._channels.get(platform)
        if channel is None:
            logger.debug("[progress] no channel registered for %s", platform)
            return False
        try:
            if not channel.is_available():
                return False
            result = channel.send_message(text, target=target)
            return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            logger.warning("[progress] %s push failed: %s", platform, exc)
            return False

    def get_platforms(self) -> list[str]:
        return list(self._channels)
