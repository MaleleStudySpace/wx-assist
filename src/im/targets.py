"""Resolve legacy and multi-platform push target values."""

import json
from typing import Any

from .config_schema import load_platforms_config


_ALIAS = {"ilink": "wechat", "wechat": "wechat", "qqbot": "qqbot", "feishu": "feishu"}


def normalize_targets(value: Any) -> list[str]:
    """Return normalized platform names; legacy strings remain supported."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                value = parsed
        except (TypeError, ValueError):
            pass
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result = []
    for item in values:
        platform = _ALIAS.get(str(item).strip(), "")
        if platform and platform not in result:
            result.append(platform)
    return result


def bound_push_targets() -> list[str]:
    """Return the currently QR-bound outbound channels.

    The returned values are actual delivery channel names.  In particular,
    WeChat remains ``ilink`` internally; ``wechat`` is only a legacy business
    alias accepted by ``normalize_targets``.
    """
    targets: list[str] = []
    try:
        from src.wechat.ilink_push import get_ilink_push
        if get_ilink_push().is_available():
            targets.append("ilink")
    except Exception:
        pass

    for config in load_platforms_config():
        extra = config.extra or {}
        if not config.enabled:
            continue
        if config.name == "qqbot":
            if extra.get("app_id") and extra.get("client_secret") and extra.get("user_openid"):
                targets.append("qqbot")
        elif config.name == "feishu":
            if extra.get("app_id") and extra.get("app_secret") and extra.get("open_id"):
                targets.append("feishu")
    return targets


def default_target(platform: str) -> str:
    for config in load_platforms_config():
        if config.name != platform:
            continue
        extra = config.extra or {}
        configured = str(extra.get("default_target", "")).strip()
        if configured:
            return configured
        # QR onboarding stores the provider user identity. Derive the target
        # for older config files that predate the explicit default_target key.
        if platform == "qqbot" and extra.get("user_openid"):
            return f"qqbot:{extra['user_openid']}"
        if platform == "feishu" and extra.get("open_id"):
            return f"feishu:open_id:{extra['open_id']}"
        return ""
    return ""
