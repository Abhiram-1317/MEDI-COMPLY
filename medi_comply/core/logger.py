"""
MEDI-COMPLY — Structured JSON logger with ``trace_id`` correlation.

Uses *structlog* for structured, JSON-formatted logging to both the console
and a rotating file.  Every log entry carries ``timestamp``, ``level``,
``agent_name``, ``trace_id``, ``action``, ``message``, and optional
``extra_data``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Optional

import structlog


_CONFIGURED = False


def _configure_stdlib_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    """Wire up stdlib root logger with console + file handlers."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler — human-readable
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # File handler — JSON lines
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "medi_comply.log"),
        maxBytes=10_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(file_handler)


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    """Initialise *structlog* with JSON rendering.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return

    _configure_stdlib_logging(log_level, log_dir)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(
    agent_name: str = "system",
    trace_id: Optional[str] = None,
) -> structlog.stdlib.BoundLogger:
    """Return a *structlog* logger pre-bound with agent context.

    Parameters
    ----------
    agent_name:
        Identifier for the agent or component emitting the log.
    trace_id:
        Optional correlation ID propagated across the workflow.
    """
    setup_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()
    bound = logger.bind(agent_name=agent_name)
    if trace_id:
        bound = bound.bind(trace_id=trace_id)
    return bound


def log_action(
    logger: structlog.stdlib.BoundLogger,
    action: str,
    message: str,
    extra_data: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Emit a structured INFO-level log entry for an agent action.

    Parameters
    ----------
    logger:
        The bound structlog logger to write through.
    action:
        Short machine-readable action tag (e.g. ``"state_transition"``).
    message:
        Human-readable description of the event.
    extra_data:
        Arbitrary key-value pairs to attach to the log record.
    trace_id:
        Override or set the trace_id for this specific entry.
    """
    kwargs: dict[str, Any] = {"action": action, "message": message}
    if extra_data:
        kwargs["extra_data"] = extra_data
    if trace_id:
        kwargs["trace_id"] = trace_id
    logger.info(**kwargs)
