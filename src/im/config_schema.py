"""Configuration model for optional IM platform adapters.

The foundation stage only provides parsing and validation.  The existing
WeChat/.env startup path continues to be authoritative until the platform
registry is explicitly integrated in a later segment.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PLATFORMS_CONFIG_PATH = Path("data/platforms.json")
SUPPORTED_TRANSPORTS = {"", "wcdb", "mac_ui", "mac_hybrid", "ilink", "qq_openapi", "feishu_webhook"}


@dataclass
class PlatformConfig:
    name: str
    enabled: bool = True
    transport: str = ""
    webhook_port: int = 0
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "PlatformConfig":
        if not isinstance(data, dict):
            raise ValueError("platform entry must be an object")
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("platform name is required")
        transport = str(data.get("transport", "")).strip()
        if transport not in SUPPORTED_TRANSPORTS:
            raise ValueError(f"unsupported platform transport: {transport}")
        port = data.get("webhook_port", 0)
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("webhook_port must be an integer between 0 and 65535")
        extra = data.get("extra", {})
        if not isinstance(extra, dict):
            raise ValueError("extra must be an object")
        return cls(
            name=name,
            enabled=bool(data.get("enabled", True)),
            transport=transport,
            webhook_port=port,
            extra=dict(extra),
        )


def load_platforms_config(path: Path = PLATFORMS_CONFIG_PATH) -> list[PlatformConfig]:
    """Load optional platform configuration; missing/invalid files degrade safely."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("platforms", [])
        if not isinstance(entries, list):
            raise ValueError("platforms must be a list")
        configs = [PlatformConfig.from_dict(item) for item in entries]
        names = [cfg.name for cfg in configs]
        if len(names) != len(set(names)):
            raise ValueError("platform names must be unique")
        return configs
    except Exception as exc:
        logger.warning("[im] failed to load %s: %s", path, exc)
        return []


def save_platforms_config(configs: list[PlatformConfig],
                          path: Path = PLATFORMS_CONFIG_PATH) -> None:
    """Atomically persist optional platform configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    payload = {"platforms": [asdict(config) for config in configs]}
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
