"""Explicit plugin and outbound-channel registries.

Plugins are registered only when their package is imported.  The foundation
segment does not import or start any plugin automatically.
"""

from typing import Any, Type

from ..base import BasePlatformAdapter

_ADAPTERS: dict[str, Type[BasePlatformAdapter]] = {}
_PUSH_CHANNELS: dict[str, Any] = {}


def register(name: str):
    def decorator(cls: Type[BasePlatformAdapter]) -> Type[BasePlatformAdapter]:
        _ADAPTERS[name] = cls
        return cls
    return decorator


def load_builtin_plugins() -> None:
    """Import built-in adapters explicitly before registry lookup."""
    from . import wechat  # noqa: F401
    try:
        from . import qqbot  # noqa: F401
    except Exception:
        # Optional QQ dependencies/config must not affect WeChat startup.
        pass


def get_plugin(name: str) -> Type[BasePlatformAdapter]:
    if name not in _ADAPTERS:
        raise KeyError(f"platform plugin is not registered: {name}")
    return _ADAPTERS[name]


def register_plugin_push_channel(name: str, channel: Any) -> None:
    _PUSH_CHANNELS[name] = channel


def get_plugin_push_channel(name: str) -> Any:
    """Return a registered channel while preserving the legacy iLink value."""
    channel = _PUSH_CHANNELS.get(name)
    if channel is not None:
        return channel
    if name == "ilink":
        from .wechat.push import get_wechat_push_channel
        return get_wechat_push_channel()
    return None
