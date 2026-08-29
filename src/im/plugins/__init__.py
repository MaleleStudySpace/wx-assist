"""Explicit plugin and outbound-channel registries.

Plugins are registered only when their package is imported.  The foundation
segment does not import or start any plugin automatically.
"""

import importlib
import logging
from typing import Any, Type

from ..base import BasePlatformAdapter

logger = logging.getLogger(__name__)

_ADAPTERS: dict[str, Type[BasePlatformAdapter]] = {}
_PUSH_CHANNELS: dict[str, Any] = {}
_PLUGIN_LOAD_ERRORS: dict[str, str] = {}


def register(name: str):
    def decorator(cls: Type[BasePlatformAdapter]) -> Type[BasePlatformAdapter]:
        _ADAPTERS[name] = cls
        return cls
    return decorator


def load_builtin_plugins() -> None:
    """Import optional adapters explicitly before registry lookup.

    The legacy WeChat path is intentionally excluded: its real runtime is
    still Bot + iLink, while ``wechat.adapter.WechatAdapter`` is only a
    migration placeholder and must not enter the optional adapter registry.
    """
    for name in ("qqbot", "feishu"):
        try:
            importlib.import_module(f"{__name__}.{name}")
            _PLUGIN_LOAD_ERRORS.pop(name, None)
        except ImportError as exc:
            _PLUGIN_LOAD_ERRORS[name] = str(exc)
            logger.error("[im] %s plugin unavailable: %s", name, exc)
        except Exception as exc:
            _PLUGIN_LOAD_ERRORS[name] = str(exc)
            logger.exception("[im] %s plugin failed to load", name)


def get_plugin_load_errors() -> dict[str, str]:
    """Return optional plugin import failures for status/reporting."""
    return dict(_PLUGIN_LOAD_ERRORS)


def clear_plugin_push_channels() -> None:
    """Remove optional outbound channels before an adapter is reloaded."""
    for name in ("qqbot", "feishu"):
        _PUSH_CHANNELS.pop(name, None)


def get_plugin(name: str) -> Type[BasePlatformAdapter]:
    if name not in _ADAPTERS:
        error = _PLUGIN_LOAD_ERRORS.get(name)
        if error:
            raise KeyError(f"platform plugin failed to load: {name}: {error}")
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
