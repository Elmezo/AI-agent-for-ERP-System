"""Structured logging configuration (structlog).

A single ``configure_logging`` call sets up structlog for the whole process.
Modules obtain a logger with ``get_logger(__name__)``. Logs can be rendered as
human-friendly console output or machine-readable JSON via ``LOG_FORMAT``.
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.config.settings import LogFormat, Settings

_CONFIGURED = False


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging once per process (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)

    # Silence noisy third-party request loggers (httpx logs every HTTP call at
    # INFO, which clutters the chat REPL). Keep warnings and above.
    for noisy in ("httpx", "httpcore", "ollama", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format is LogFormat.JSON:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
