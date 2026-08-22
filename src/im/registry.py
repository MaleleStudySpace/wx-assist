"""Lifecycle registry for optional IM adapters.

This registry is not connected to Bot during the foundation segment.  Keeping
that boundary explicit prevents a new configuration file from changing the
existing WeChat startup path before the adapter migration is accepted.
"""

import logging
import threading
from typing import Callable, Optional

from .base import BasePlatformAdapter, MessageCallback
from .config_schema import PlatformConfig
from .message import NormalizedMessage

logger = logging.getLogger(__name__)

_GLOBAL_REGISTRY: Optional["PlatformRegistry"] = None
_GLOBAL_LOCK = threading.Lock()


def set_global_registry(registry: "PlatformRegistry") -> None:
    global _GLOBAL_REGISTRY
    with _GLOBAL_LOCK:
        _GLOBAL_REGISTRY = registry


def get_global_registry() -> Optional["PlatformRegistry"]:
    with _GLOBAL_LOCK:
        return _GLOBAL_REGISTRY


class PlatformRegistry:
    """Start/stop optional adapters with failure isolation."""

    def __init__(self, factory: Optional[Callable[[PlatformConfig], BasePlatformAdapter]] = None) -> None:
        self._factory = factory
        self._adapters: dict[str, BasePlatformAdapter] = {}
        self._configs: dict[str, PlatformConfig] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.RLock()

    def start_all(self, configs: list[PlatformConfig],
                  message_callback: MessageCallback,
                  on_adapter: Optional[Callable[[PlatformConfig, BasePlatformAdapter], None]] = None) -> dict:
        started: list[str] = []
        failed: dict[str, str] = {}
        with self._lock:
            for config in configs:
                if not config.enabled:
                    continue
                try:
                    adapter = self._instantiate(config)
                    if not adapter.start(message_callback, groups=[]):
                        raise RuntimeError("adapter.start returned False")
                    self._adapters[config.name] = adapter
                    self._configs[config.name] = config
                    self._errors.pop(config.name, None)
                    started.append(config.name)
                    if on_adapter is not None:
                        on_adapter(config, adapter)
                except Exception as exc:
                    self._errors[config.name] = str(exc)
                    failed[config.name] = str(exc)
                    logger.exception("[im] platform %s failed to start", config.name)
        return {"started": started, "failed": failed}

    def stop_all(self) -> None:
        with self._lock:
            adapters = list(self._adapters.items())
            self._adapters.clear()
        for name, adapter in adapters:
            try:
                adapter.stop()
            except Exception as exc:
                logger.warning("[im] platform %s failed to stop: %s", name, exc)

    def get_health(self) -> dict:
        with self._lock:
            names = set(self._configs) | set(self._errors)
            result = {}
            for name in names:
                if name in self._errors:
                    result[name] = {"ok": False, "detail": self._errors[name]}
                    continue
                try:
                    result[name] = self._adapters[name].health_status()
                except Exception as exc:
                    result[name] = {"ok": False, "detail": str(exc)}
            return result

    def get_adapter(self, name: str) -> Optional[BasePlatformAdapter]:
        with self._lock:
            return self._adapters.get(name)

    def _instantiate(self, config: PlatformConfig) -> BasePlatformAdapter:
        if self._factory is not None:
            return self._factory(config)
        from .plugins import load_builtin_plugins, get_plugin
        load_builtin_plugins()
        return get_plugin(config.name)(config)
