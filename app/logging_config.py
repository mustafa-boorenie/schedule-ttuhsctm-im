"""
Logging configuration for the application.
"""
import logging
import sys
from typing import Optional

from .settings import settings


def setup_logging(level: Optional[str] = None) -> None:
    """
    Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to DEBUG if debug=True.
    """
    if level is None:
        level = "DEBUG" if settings.debug else "INFO"

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler with formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    # Format: timestamp - level - logger name - message
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Set specific log levels for noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.debug else logging.WARNING
    )
    logging.getLogger("apscheduler").setLevel(logging.INFO)

    # Log startup info
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured at {level} level")
    if settings.debug:
        logger.debug("Debug mode is enabled")


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)
