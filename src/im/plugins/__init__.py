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


def get_plugin(name: str) -> Type[BasePlatformAdapter]:
    if name not in _ADAPTERS:
        raise KeyError(f"platform plugin is not registered: {name}")
    return _ADAPTERS[name]


def register_plugin_push_channel(name: str, channel: Any) -> None:
    _PUSH_CHANNELS[name] = channel


def get_plugin_push_channel(name: str) -> Any:
    return _PUSH_CHANNELS.get(name)
