"""Platform adapter contracts for the synchronous IM layer."""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from .message import NormalizedMessage, SessionSource


MessageCallback = Callable[[NormalizedMessage], None]


class BasePlatformAdapter(ABC):
    """Minimal synchronous contract shared by IM platform adapters.

    Existing WeChat backends are intentionally not changed in the foundation
    stage.  New adapters can implement this contract without forcing the
    current synchronous bot onto an asyncio event loop.
    """

    platform_name: str = ""
    display_name: str = ""

    supports_image: bool = False
    supports_voice: bool = False
    supports_video: bool = False
    supports_file: bool = False
    supports_edit_message: bool = False
    supports_typing_indicator: bool = False
    supports_threads: bool = False
    supports_reactions: bool = False
    text_message_max_len: int = 0

    @abstractmethod
    def start(self, callback: MessageCallback,
              groups: list[str] | None = None) -> bool:
        """Start the platform reader and deliver normalized messages."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the platform reader without blocking the main bot forever."""

    @abstractmethod
    def send_text(self, chat_id: str, content: str,
                  *, reply_to: Optional[str] = None) -> bool:
        """Send plain text to a platform-native target."""

    @abstractmethod
    def health_status(self) -> dict:
        """Return a small, serializable health snapshot."""

    @abstractmethod
    def list_chats(self) -> list[SessionSource]:
        """Return known conversations available for configuration."""

    @staticmethod
    def namespace(platform: str, native_id: str) -> str:
        """Build a stable platform namespace without double-prefixing."""
        prefix = str(platform or "").strip()
        native = str(native_id or "")
        if not prefix:
            return native
        marker = f"{prefix}:"
        return native if native.startswith(marker) else f"{marker}{native}"

    @staticmethod
    def parse(chat_id: str) -> tuple[str, str]:
        """Split ``platform:native_id``; legacy unnamespaced IDs are preserved."""
        value = str(chat_id or "")
        if ":" not in value:
            return "", value
        return tuple(value.split(":", 1))  # type: ignore[return-value]
