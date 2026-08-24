"""Lifecycle registry for optional IM adapters."""

import logging
import threading
from typing import Callable, Optional

from .base import BasePlatformAdapter, MessageCallback
from .config_schema import PlatformConfig

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
        self._message_callback: Optional[MessageCallback] = None
        self._on_adapter: Optional[Callable[[PlatformConfig, BasePlatformAdapter], None]] = None
        self._lock = threading.RLock()

    def start_all(self, configs: list[PlatformConfig],
                  message_callback: MessageCallback,
                  on_adapter: Optional[Callable[[PlatformConfig, BasePlatformAdapter], None]] = None) -> dict:
        self._message_callback = message_callback
        self._on_adapter = on_adapter
        started: list[str] = []
        failed: dict[str, str] = {}
        for config in configs:
            if not config.enabled:
                continue
            result = self.start_one(config, message_callback, on_adapter)
            if result.get("ok"):
                started.append(config.name)
            else:
                failed[config.name] = result.get("error", "start failed")
        return {"started": started, "failed": failed}

    def start_one(self, config: PlatformConfig,
                  message_callback: Optional[MessageCallback] = None,
                  on_adapter: Optional[Callable[[PlatformConfig, BasePlatformAdapter], None]] = None) -> dict:
        """Replace one optional adapter without touching other platforms."""
        self.stop_one(config.name)
        callback = message_callback or self._message_callback
        hook = on_adapter if on_adapter is not None else self._on_adapter
        with self._lock:
            self._configs[config.name] = config
        if not config.enabled:
            with self._lock:
                self._errors.pop(config.name, None)
            return {"ok": True, "status": "disabled"}
        if callback is None:
            return {"ok": False, "status": "failed", "error": "message callback is not configured"}
        try:
            adapter = self._instantiate(config)
            if not adapter.start(callback, groups=[]):
                raise RuntimeError("adapter.start returned False")
            with self._lock:
                self._adapters[config.name] = adapter
                self._errors.pop(config.name, None)
            if hook is not None:
                hook(config, adapter)
            return {"ok": True, "status": "started"}
        except Exception as exc:
            with self._lock:
                self._errors[config.name] = str(exc)
            logger.exception("[im] platform %s failed to start", config.name)
            return {"ok": False, "status": "failed", "error": str(exc)}

    def stop_one(self, name: str) -> None:
        with self._lock:
            adapter = self._adapters.pop(name, None)
        if adapter is not None:
            try:
                adapter.stop()
            except Exception as exc:
                logger.warning("[im] platform %s failed to stop: %s", name, exc)

    def stop_all(self) -> None:
        with self._lock:
            names = list(self._adapters)
        for name in names:
            self.stop_one(name)

    def get_health(self) -> dict:
        with self._lock:
            names = set(self._configs) | set(self._errors)
            result = {}
            for name in names:
                if name in self._errors:
                    result[name] = {"ok": False, "detail": self._errors[name]}
                    continue
                adapter = self._adapters.get(name)
                if adapter is None:
                    result[name] = {"ok": False, "detail": "未启动"}
                    continue
                try:
                    result[name] = adapter.health_status()
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
