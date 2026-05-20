"""Loguru-based logger with daily rotation."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger
from loguru._logger import Logger

_configured = False


def setup_logger(log_dir: Path | str | None = None, level: str = "INFO") -> None:
    """Configure loguru with console + daily rotating file."""
    global _configured
    if _configured:
        return

    logger.remove()
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<8} | {message}")

    if log_dir is None:
        log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_dir / "{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
    )
    _configured = True


def get_logger() -> Logger:
    """Return the configured loguru logger."""
    if not _configured:
        setup_logger()
    return logger  # type: ignore[return-value]
