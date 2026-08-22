"""Outbound push channel contract for platform-specific delivery."""

from abc import ABC, abstractmethod
from typing import Callable, Optional


class PushChannel(ABC):
    """A platform-bound outbound delivery channel.

    ``wechat_ilink`` remains a WeChat-specific implementation.  The contract
    only standardizes how higher-level code asks a channel to deliver; it does
    not turn iLink into a cross-platform protocol.
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Stable channel identifier used in delivery records."""

    @property
    @abstractmethod
    def platform(self) -> str:
        """Platform owned by this channel, e.g. ``wechat``."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether configuration/authentication permits a send attempt."""

    @abstractmethod
    def is_healthy(self) -> bool:
        """Whether the most recent health state is usable."""

    @abstractmethod
    def send_message(self, text: str, *,
                     progress_callback: Optional[Callable] = None,
                     target: Optional[str] = None) -> dict:
        """Send text and return ``success`` plus optional ``error``."""

    @abstractmethod
    def get_status(self) -> dict:
        """Return a serializable status snapshot for the Web UI."""
