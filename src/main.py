"""WeChat Group Chat Summarizer Bot — Entry Point.

Usage:
    python -m src.main
    python -m src.main --dry-run
"""

import sys

from .config import load_config, BotConfig


def _mask(s: str, keep: int = 6) -> str:
    """Mask a secret string, showing only the first and last few characters."""
    if not s:
        return "(not set)"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "***" + s[-keep:]


def _print_config_summary(config: BotConfig) -> None:
    """Print a human-readable summary of the loaded configuration (secrets masked)."""
    print()
    print("Configuration Summary")
    print("─" * 50)
    print(f"  AI Provider Model:      {config.ai_provider_model or '(not set)'}")
    print(f"  AI Provider API Key:    {_mask(config.ai_provider_api_key)}")
    print(f"  WeChat Backend:         {config.wechat_backend}")
    print(f"  Admin wxid:              {config.admin_wxid or '(not set)'}")
    print(f"  DB Path:                 {config.db_path}")
    print(f"  Poll Interval (sec):     {config.poll_interval_sec}")
    print(f"  Chunk Size:              {config.chunk_size}")
    print(f"  Log Level:               {config.log_level}")
    print(f"  Log File:                {config.log_file}")
    print("─" * 50)
    print()


def main() -> None:
    """Load configuration and start the bot.

    With --dry-run: validate .env config and print a summary, then exit.
    """
    dry_run = "--dry-run" in sys.argv

    config = load_config()

    if dry_run:
        _print_config_summary(config)
        print("Dry-run mode: config loaded successfully. Bot would start polling now.")
        return

    from .bot import Bot
    bot = Bot(config)
    bot.run()


if __name__ == "__main__":
    main()
