"""
Application-wide exception hierarchy.

Design decision: Use a typed exception hierarchy so that:
1. Domain errors never leak infrastructure details
2. Each layer catches only the exceptions it can handle
3. FastAPI exception handlers can map to HTTP status codes cleanly

Hierarchy:
  AppError                    (base)
  ├── DomainError             (business rule violations)
  │   ├── ValidationError     (invalid input)
  │   ├── NotFoundError       (entity not found)
  │   ├── ConflictError       (state conflicts)
  │   └── BusinessRuleError   (domain invariant violated)
  ├── InfrastructureError     (external systems)
  │   ├── ExchangeError       (exchange API failures)
  │   ├── LLMError            (LLM provider failures)
  │   ├── DatabaseError       (DB errors)
  │   └── CacheError          (Redis errors)
  ├── SecurityError           (auth/authz)
  │   ├── AuthenticationError
  │   └── AuthorizationError
  └── RiskViolationError      (risk management — always escalated)
      ├── DrawdownLimitError
      ├── PositionLimitError
      └── CircuitBreakerError
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base exception for all application errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__
        self.details = details or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"


# ---------------------------------------------------------------------------
# Domain Errors
# ---------------------------------------------------------------------------


class DomainError(AppError):
    """Base class for domain business rule violations."""


class ValidationError(DomainError):
    """Raised when input data fails domain validation."""

    def __init__(self, message: str, field: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.field = field


class NotFoundError(DomainError):
    """Raised when a requested entity cannot be found."""

    def __init__(self, entity: str, entity_id: Any, **kwargs: Any) -> None:
        super().__init__(f"{entity} with id={entity_id!r} not found", **kwargs)
        self.entity = entity
        self.entity_id = entity_id


class ConflictError(DomainError):
    """Raised when an operation conflicts with existing state."""


class BusinessRuleError(DomainError):
    """Raised when a domain invariant is violated."""


# ---------------------------------------------------------------------------
# Infrastructure Errors
# ---------------------------------------------------------------------------


class InfrastructureError(AppError):
    """Base class for external system failures."""


class ExchangeError(InfrastructureError):
    """Exchange API failure or unexpected response."""

    def __init__(self, message: str, exchange: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.exchange = exchange


class ExchangeRateLimitError(ExchangeError):
    """Exchange rate limit exceeded."""


class ExchangeOrderError(ExchangeError):
    """Order placement/cancellation failure."""


class LLMError(InfrastructureError):
    """LLM provider failure."""

    def __init__(self, message: str, provider: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.provider = provider


class LLMRateLimitError(LLMError):
    """LLM provider rate limit exceeded."""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        retry_after_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, provider=provider, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class LLMQuotaError(LLMError):
    """The provider account cannot serve requests (no balance or bad credentials).

    Unlike a rate limit, this condition does not fix itself in seconds, so the
    gateway parks the provider for a longer cooldown instead of retrying it on
    every analysis cycle.
    """

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        cooldown_seconds: int = 1800,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, provider=provider, **kwargs)
        self.cooldown_seconds = cooldown_seconds


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


class DatabaseError(InfrastructureError):
    """Database operation failure."""


class CacheError(InfrastructureError):
    """Cache (Redis) operation failure."""


# ---------------------------------------------------------------------------
# Security Errors
# ---------------------------------------------------------------------------


class SecurityError(AppError):
    """Base class for security-related errors."""


class AuthenticationError(SecurityError):
    """Invalid credentials or missing authentication."""


class AuthorizationError(SecurityError):
    """Insufficient permissions for the requested operation."""


# ---------------------------------------------------------------------------
# Risk Management Errors — Critical, always escalated
# ---------------------------------------------------------------------------


class RiskViolationError(AppError):
    """
    Base class for risk management violations.

    These are ALWAYS escalated and never silently caught.
    They trigger the circuit breaker or halt trading operations.
    """

    def __init__(self, message: str, severity: str = "HIGH", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.severity = severity


class DrawdownLimitError(RiskViolationError):
    """Daily drawdown limit exceeded — halt all trading."""

    def __init__(self, current_loss_pct: float, limit_pct: float) -> None:
        super().__init__(
            f"Daily drawdown limit exceeded: {current_loss_pct:.2f}% loss "
            f"(limit: {limit_pct:.2f}%)",
            severity="CRITICAL",
            details={"current_loss_pct": current_loss_pct, "limit_pct": limit_pct},
        )
        self.current_loss_pct = current_loss_pct
        self.limit_pct = limit_pct


class PositionLimitError(RiskViolationError):
    """Position size exceeds configured risk limits."""

    def __init__(self, requested_pct: float, limit_pct: float) -> None:
        super().__init__(
            f"Position size {requested_pct:.2f}% exceeds limit {limit_pct:.2f}%",
            severity="HIGH",
            details={"requested_pct": requested_pct, "limit_pct": limit_pct},
        )


class CircuitBreakerError(RiskViolationError):
    """Circuit breaker tripped — system is in emergency halt mode."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Circuit breaker activated: {reason}",
            severity="CRITICAL",
            details={"reason": reason},
        )


class DuplicateOrderError(RiskViolationError):
    """Duplicate order detected — preventing double execution."""

    def __init__(self, order_hash: str) -> None:
        super().__init__(
            f"Duplicate order detected with hash={order_hash!r}",
            severity="HIGH",
            details={"order_hash": order_hash},
        )


class UnauthorizedTradingModeError(SecurityError):
    """Attempt to execute real trading without explicit authorization."""

    def __init__(self) -> None:
        super().__init__(
            "Real trading is not enabled. Set ENABLE_REAL_TRADING=true "
            "and EXECUTION_MODE=real explicitly to enable live trading.",
        )
