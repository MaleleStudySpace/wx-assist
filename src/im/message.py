"""Platform-neutral message and session models.

The legacy WeChat readers still emit dictionaries during the migration.  The
conversion helpers are intentionally not called by stages 1-3; they are the
boundary used when a new adapter is connected.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    EMOJI = "emoji"
    LINK = "link"
    SYSTEM = "system"


@dataclass(frozen=True)
class SessionSource:
    """Platform, native conversation and sender identity."""

    platform: str
    native_chat_id: str
    chat_type: str
    user_id: str = ""
    thread_id: Optional[str] = None
    display_name: str = ""


@dataclass
class NormalizedMessage:
    """A platform-neutral inbound message.

    Stages 1-3 do not call :meth:`from_legacy_dict`; existing readers and
    Router continue to use their original dictionaries.  The method becomes
    the adapter boundary when a new platform is connected in stage 4.
    """

    platform: str
    native_message_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    sender_name: str
    content: str
    message_type: MessageType = MessageType.TEXT
    timestamp: int = 0
    media_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_legacy_dict(self) -> dict[str, Any]:
        """Convert to the dictionary contract used by the current Router."""
        msg_type = {
            MessageType.TEXT: 1,
            MessageType.IMAGE: 3,
            MessageType.VOICE: 34,
            MessageType.EMOJI: 47,
            MessageType.LINK: 49,
            MessageType.VIDEO: 43,
            MessageType.FILE: 49,
            MessageType.SYSTEM: 10000,
        }.get(self.message_type, 1)
        return {
            "message_id": self.native_message_id,
            "chat_id": self.chat_id,
            "group_name": "",
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "msg_type": msg_type,
            "timestamp": self.timestamp,
            "is_group": self.chat_type == "group",
        }

    @classmethod
    def from_legacy_dict(
        cls,
        data: dict[str, Any],
        platform: str = "wechat",
    ) -> "NormalizedMessage":
        """Convert a legacy message dictionary at a platform boundary.

        **Stages 1-3 must not call this method.** Existing WeChat/iLink
        readers must keep emitting and storing their original chat IDs.  The
        QQ adapter uses this boundary in stage 4 when namespaced IDs are
        introduced (for example ``qqbot:openid``).
        """
        native_chat_id = str(data.get("chat_id", ""))
        chat_prefix = f"{platform}:"
        chat_id = (
            native_chat_id
            if native_chat_id.startswith(chat_prefix)
            else f"{chat_prefix}{native_chat_id}"
        )
        native_sender_id = str(data.get("sender_id", ""))
        sender_id = (
            native_sender_id
            if not native_sender_id or native_sender_id.startswith(chat_prefix)
            else f"{chat_prefix}{native_sender_id}"
        )
        return cls(
            platform=platform,
            native_message_id=str(data.get("message_id", "")),
            chat_id=chat_id,
            chat_type="group" if data.get("is_group") else "dm",
            sender_id=sender_id,
            sender_name=str(data.get("sender_name", "")),
            content=str(data.get("content", "")),
            message_type=MessageType.TEXT,
            timestamp=int(data.get("timestamp", 0) or 0),
            raw=dict(data),
        )
