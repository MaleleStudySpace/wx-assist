"""Configuration loading from .env file."""

import logging
import msvcrt
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _decode_wechat_groups(raw: str) -> str:
    """Decode URL-encoded group names from .env WECHAT_GROUPS value.

    We store each group name URL-encoded (via encodeURIComponent / urllib.parse.quote)
    so that commas, equals signs, and newlines in real group names don't break the
    .env format or our comma-separated delimiter.  This function reverses that encoding
    with a fallback: if decoding a chunk doesn't change it (or raises), the original
    is kept — for backward compatibility with old unencoded .env files.
    """
    if not raw or raw.strip() == "*":
        return raw.strip() if raw else "*"
    decoded = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            d = unquote(chunk)
            decoded.append(d)
        except Exception:
            decoded.append(chunk)
    return ",".join(decoded) if decoded else "*"

def _resolve_project_root() -> Path:
    app_home = os.getenv("WEBOT_APP_HOME", "").strip()
    if app_home:
        return Path(app_home).expanduser().resolve()

    # In a PyInstaller EXE, __file__ resolves inside the temp extraction dir.
    # We want writable data (.env, data/*) in the EXE directory, not in the temp dir.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _resolve_project_root()


def write_env_atomic(env_path: Path, updates: dict[str, str]) -> None:
    """Thread- and process-safe atomic update of .env key=value pairs.

    Uses a file lock (.env.lock) via msvcrt (Windows) to prevent concurrent
    writes from overwriting each other.  Reads the existing file, applies
    the updates dict, and writes back atomically via os.replace().

    Args:
        env_path: Path to the .env file.
        updates: Dict of key->value pairs to set or add.
    """
    lock_path = env_path.with_suffix(".lock")

    # Create the .env file if it doesn't exist
    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("", encoding="utf-8")

    lock_fd = open(lock_path, "w")
    try:
        # Acquire exclusive lock (blocking with short retry)
        for _ in range(10):
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                import time
                time.sleep(0.05)
        else:
            # Could not acquire lock after 10 tries — proceed anyway
            # (better to risk a rare race than to hang forever)
            logger.warning("Could not acquire .env.lock after 10 attempts, proceeding without lock")

        # Read existing content
        lines = []
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []

        # Apply updates
        updated_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            updated = False
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}")
                    updated_keys.add(k)
                    updated = True
            if not updated:
                new_lines.append(line)

        for k, v in updates.items():
            if k not in updated_keys:
                new_lines.append(f"{k}={v}")

        # Atomic write via temp file
        tmp = env_path.with_suffix(".tmp")
        tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.replace(tmp, env_path)

    finally:
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        lock_fd.close()


def find_env_file() -> Path | None:
    """Find the .env file using a consistent search order.

    Order: WEBOT_ENV_FILE override → EXE directory (frozen) → project root → current working directory.
    Returns the Path if found, or None if no .env exists anywhere.
    """
    explicit_env = os.getenv("WEBOT_ENV_FILE", "").strip()
    if explicit_env:
        explicit_path = Path(explicit_env).expanduser()
        if explicit_path.exists():
            return explicit_path

    locations = [
        PROJECT_ROOT / ".env",
        Path.cwd() / ".env",
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        locations.insert(0, exe_dir / ".env")

    for loc in locations:
        if loc.exists():
            return loc
    return None


_env_path = find_env_file()

if _env_path:
    load_dotenv(_env_path)
else:
    load_dotenv()

# Log which .env was loaded (helpful for debugging EXE packaging issues)
if _env_path:
    logger.info("Loaded .env from: %s", _env_path)
else:
    _search_locations = [
        PROJECT_ROOT / ".env",
        Path.cwd() / ".env",
    ]
    if getattr(sys, "frozen", False):
        _search_locations.insert(0, Path(sys.executable).resolve().parent / ".env")
    logger.warning(
        ".env not found in any search path (%s). Using defaults.",
        ", ".join(str(p) for p in _search_locations),
    )


@dataclass
class BotConfig:
    """All configuration for the WeChat summarizer bot."""

    # === AI Provider (unified config) ===
    # Base URL of the AI provider (e.g. https://api.deepseek.com).
    ai_provider_base_url: str = ""
    ai_provider_api_key: str = ""
    # "auto" | "anthropic" | "openai" | "custom"
    ai_provider_type: str = "openai"
    ai_provider_model: str = ""
    # JSON string for extra_body, e.g. '{"thinking":{"type":"disabled"}}'
    ai_provider_extra_body: str = ""

    # === WeChat Backend ===
    wechat_backend: str = "wcdb"
    # Custom WeChat data directory. Leave empty to auto-detect from Documents.
    # Set to the parent directory containing wxid_* folders (e.g. D:\WeChatData).
    wechat_data_dir: str = ""

    # === Bot Identity ===
    # Admin wxid (can manage nicknames and bot settings)
    admin_wxid: str = ""

    # === Trigger Keywords ===
    trigger_keywords: list[str] = field(default_factory=lambda: [
        "总结一下", "之前发了什么", "错过了什么", "summarize",
        "what did i miss", "聊天总结", "帮我总结", "前面说了什么",
        "说了啥", "发生了什么",
    ])

    # === Database ===
    db_path: str = "data/messages.db"

    # === Restricted Features ===
    # Master switch for sensitive features: message anti-revoke, SNS delete protection.
    # When false, these features are completely disabled and hidden from the UI.
    enable_restricted_features: bool = False

    # === Memory Consolidation ===
    # When enabled, automatically consolidates group chat messages into a
    # "memory diary" via AI (every 50 msgs or 1 hour).  This memory is then
    # injected into @mention chat and proactive chat prompts as context.
    memory_consolidation_enabled: bool = False

    # === Tuning ===
    poll_interval_sec: float = 1.0
    chunk_size: int = 400

    # === Logging ===
    log_level: str = "INFO"
    log_file: str = "data/bot.log"


def _validate_config(kwargs: dict) -> None:
    """Validate numeric config values.  Prints clear errors and exits on bad values."""
    errors: list[str] = []

    # poll_interval_sec
    poll_interval_sec = kwargs.get("poll_interval_sec", 1.0)
    if poll_interval_sec < 0.1:
        errors.append(
            f"POLL_INTERVAL_SEC must be >= 0.1, got {poll_interval_sec}"
        )

    # chunk_size
    chunk_size = kwargs.get("chunk_size", 400)
    if not (10 <= chunk_size <= 1000):
        errors.append(
            f"CHUNK_SIZE must be between 10 and 1000, got {chunk_size}"
        )

    # max_retries (if present in config)
    max_retries = kwargs.get("max_retries")
    if max_retries is not None:
        if not (1 <= max_retries <= 10):
            errors.append(
                f"MAX_RETRIES must be between 1 and 10, got {max_retries}"
            )

    if errors:
        msg = "配置值无效:\n" + "\n".join(f"  - {err}" for err in errors)
        raise RuntimeError(msg)


def load_config() -> BotConfig:
    """Load configuration from environment variables.

    Returns a validated BotConfig instance.
    Raises RuntimeError if required configuration is missing.
    """
    # Validate required AI provider config.
    # If no AI config is present, allow startup without keys — the
    # user can configure AI later via the dashboard.
    ai_provider_base_url = os.getenv("AI_PROVIDER_BASE_URL", "").strip()
    ai_provider_api_key = os.getenv("AI_PROVIDER_API_KEY", "").strip()
    # No strict validation here — AI features will simply be unavailable
    # until the user configures AI_PROVIDER_* via the dashboard.

    # Parse trigger keywords from comma-separated string
    keywords_str = os.getenv("TRIGGER_KEYWORDS", "").strip()
    trigger_keywords = (
        [kw.strip() for kw in keywords_str.split(",") if kw.strip()]
        if keywords_str
        else None  # let the dataclass default apply
    )

    kwargs: dict = {
        "wechat_backend": os.getenv("WECHAT_BACKEND", "wcdb").strip(),
        "wechat_data_dir": os.getenv("WECHAT_DATA_DIR", "").strip(),
        "admin_wxid": os.getenv("ADMIN_WXID", "").strip(),
        "db_path": os.getenv("DB_PATH", "data/messages.db").strip(),
        "poll_interval_sec": float(os.getenv("POLL_INTERVAL_SEC", "1.0")),
        "chunk_size": int(os.getenv("CHUNK_SIZE", "400")),
        "enable_restricted_features": os.getenv("ENABLE_RESTRICTED_FEATURES", "false").strip().lower() == "true",
        "memory_consolidation_enabled": os.getenv("MEMORY_CONSOLIDATION_ENABLED", "false").strip().lower() == "true",
        "log_level": os.getenv("LOG_LEVEL", "INFO").strip(),
        "log_file": os.getenv("LOG_FILE", "data/bot.log").strip(),
        # AI Provider unified config
        "ai_provider_base_url": ai_provider_base_url,
        "ai_provider_api_key": ai_provider_api_key,
        "ai_provider_type": os.getenv("AI_PROVIDER_TYPE", "openai").strip(),
        "ai_provider_model": os.getenv("AI_PROVIDER_MODEL", "").strip(),
        "ai_provider_extra_body": os.getenv("AI_PROVIDER_EXTRA_BODY", "").strip(),
    }

    if trigger_keywords is not None:
        kwargs["trigger_keywords"] = trigger_keywords

    _validate_config(kwargs)

    return BotConfig(**kwargs)


def is_onboarding_done() -> bool:
    """Check if onboarding has been completed without loading full config.

    Uses find_env_file() for consistent .env resolution.
    """
    env_path = find_env_file()
    if env_path and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ONBOARDING_DONE="):
                return line.split("=", 1)[1].strip().lower() == "true"
        return False  # .env exists but no ONBOARDING_DONE key
    return False  # No .env found
