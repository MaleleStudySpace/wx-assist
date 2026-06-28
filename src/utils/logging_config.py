"""Logging configuration for the bot.

Uses RotatingFileHandler to prevent unbounded log growth.
Default: 10 MB per file, keep 3 backups (max ~40 MB total).
"""

import logging
import sys
from pathlib import Path


# Log rotation settings
LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
LOG_BACKUP_COUNT = 3                # keep 3 rotated files + 1 active = ~40 MB


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure root logger and quieten noisy third-party loggers.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to a log file. If None, logs to console only.
            When provided, uses RotatingFileHandler with size-based rotation
            to prevent unbounded disk usage.
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on reload
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt))
    root_logger.addHandler(console_handler)

    # File handler (optional, with rotation)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # If an existing non-rotated log is huge, truncate it on first use
        # to avoid carrying forward a 100+ MB file into the rotation chain.
        if log_path.exists() and log_path.stat().st_size > LOG_MAX_BYTES:
            try:
                # Keep only the last ~1 MB of the old log so we don't lose
                # recent context, but avoid bloating the first rotation.
                tail_bytes = 1 * 1024 * 1024
                with open(log_path, "rb") as f:
                    f.seek(max(0, f.seek(0, 2) - tail_bytes))
                    tail = f.read()
                with open(log_path, "wb") as f:
                    # Discard the partial first line
                    newline_idx = tail.find(b"\n")
                    if newline_idx >= 0:
                        f.write(tail[newline_idx + 1:])
                    else:
                        f.write(tail)
            except Exception:
                pass  # If truncation fails, rotation will still work

        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
