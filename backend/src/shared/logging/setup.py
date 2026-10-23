"""
Structured logging configuration using structlog.

Design decisions:
- structlog over stdlib logging: better async support, structured output, context binding
- JSON in production/paper, human-readable console in development
- Every log entry includes: timestamp, level, service, trace_id, request_id
- Sensitive data (API keys, passwords) are automatically redacted
- Context binding via contextvars (works correctly with asyncio)
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from structlog.types import EventDict, Processor

# ---------------------------------------------------------------------------
# Sensitive field redaction
# ---------------------------------------------------------------------------

_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "secret",
        "password",
        "token",
        "authorization",
        "api_secret",
        "private_key",
        "encryption_key",
        "jwt",
        "access_token",
        "refresh_token",
    }
)


def _redact_sensitive_fields(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Redact sensitive fields from log output."""
    for key in list(event_dict.keys()):
        if any(sensitive in key.lower() for sensitive in _SENSITIVE_FIELDS):
            event_dict[key] = "**REDACTED**"
    return event_dict


# ---------------------------------------------------------------------------
# Add service context to every log entry
# ---------------------------------------------------------------------------


def _add_service_context(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Inject service-level context into every log record, only if OpenTelemetry is enabled."""
    from src.shared.config.settings import get_settings

    s = get_settings()
    # If OpenTelemetry is disabled, skip adding service context to avoid unnecessary fields.
    if not getattr(s, "otel_enabled", False):
        return event_dict
    event_dict.setdefault("service", s.otel_service_name)
    event_dict.setdefault("env", s.app_env.value)
    event_dict.setdefault("version", s.app_version)
    return event_dict


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
) -> None:
    """
    Configure structlog for the application.

    Args:
        log_level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Output format — "json" for production, "console" for dev
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # Async context (request_id, etc.)
        structlog.stdlib.add_log_level,  # Add "level" key
        structlog.stdlib.add_logger_name,  # Add "logger" key
        structlog.processors.TimeStamper(fmt="iso"),  # ISO 8601 timestamp
        structlog.processors.StackInfoRenderer(),  # Stack traces
        _add_service_context,  # service, env, version
        _redact_sensitive_fields,  # Remove secrets
    ]

    if log_format == "json":
        # Production: structured JSON for log aggregators (Loki, CloudWatch, etc.)
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Development: colorized, human-readable output
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
        )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to route through structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Silence noisy third-party loggers
    for noisy_logger in ("uvicorn.access", "httpx", "ccxt"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> Any:
    """
    Get a structlog logger instance.

    Usage:
        logger = get_logger(__name__)
        logger.info("order_placed", order_id=str(order.id), symbol=order.symbol)
    """
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Context binding helpers (for request scoping)
# ---------------------------------------------------------------------------


def bind_request_context(
    request_id: str,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Bind request-scoped context to all logs within this async context."""
    ctx: dict[str, Any] = {"request_id": request_id}
    if user_id:
        ctx["user_id"] = user_id
    if trace_id:
        ctx["trace_id"] = trace_id
    structlog.contextvars.bind_contextvars(**ctx)


def clear_request_context() -> None:
    """Clear request-scoped context (call at end of request)."""
    structlog.contextvars.clear_contextvars()
