"""
LLM Gateway — Multi-provider abstraction layer.

Routes LLM requests to the appropriate provider based on configuration.
Provides a unified interface regardless of whether the underlying model
is OpenAI, Anthropic, Gemini, Ollama, or OpenRouter.

Design decisions:
- Provider is selected per-request (not global singleton)
- Each provider has independent retry logic and rate limiting
- Prompt/response is always returned for auditability
- Cost tracking (tokens) built in from day 1
- Async-first: all completions are coroutines
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError
from src.shared.logging.setup import get_logger
from src.shared.observability.metrics import (
    llm_errors_total,
    llm_requests_total,
    llm_tokens_used_total,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data Contracts
# ---------------------------------------------------------------------------


@dataclass
class LLMMessage:
    """A single message in a conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMRequest:
    """Unified LLM request structure."""

    messages: list[LLMMessage]
    model: str
    max_tokens: int = 4096
    temperature: float = 0.1
    system_prompt: str | None = None
    response_format: str = "text"  # "text" | "json"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Unified LLM response structure."""

    content: str
    model: str
    provider: LLMProvider

    # Token usage (for cost tracking)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Auditability
    raw_response: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"


# ---------------------------------------------------------------------------
# Provider Interface
# ---------------------------------------------------------------------------


class BaseLLMAdapter(abc.ABC):
    """Abstract base for all LLM provider adapters."""

    @property
    @abc.abstractmethod
    def provider(self) -> LLMProvider: ...

    @abc.abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Execute a completion request.

        Args:
            request: Unified request structure

        Returns:
            LLMResponse with content and token usage

        Raises:
            LLMRateLimitError: Provider rate limit exceeded
            LLMTimeoutError: Request timed out
            LLMError: Any other provider error
        """
        ...

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is accessible."""
        ...


# ---------------------------------------------------------------------------
# Gateway (Router)
# ---------------------------------------------------------------------------


class LLMGateway:
    """
    Routes LLM requests to the appropriate provider.

    Acts as the single point of entry for all LLM interactions in the system.
    Handles provider selection, fallback, metrics, and audit logging.
    """

    def __init__(self, adapters: dict[LLMProvider, BaseLLMAdapter]) -> None:
        self._adapters = adapters
        self._settings = get_settings()

    async def complete(
        self,
        request: LLMRequest,
        provider: LLMProvider | None = None,
        fallback_providers: list[LLMProvider] | None = None,
    ) -> LLMResponse:
        """
        Execute completion with automatic fallback.

        Args:
            request: The LLM request
            provider: Override provider for this request
            fallback_providers: List of providers to try if primary fails

        Returns:
            LLMResponse from the first successful provider
        """
        providers_to_try = self._build_provider_list(provider, fallback_providers)

        last_error: Exception | None = None

        for p in providers_to_try:
            adapter = self._adapters.get(p)
            if adapter is None:
                logger.warning("llm_provider_not_configured", provider=p.value)
                continue

            try:
                logger.debug(
                    "llm_request_start",
                    provider=p.value,
                    model=request.model,
                    prompt_length=sum(len(m.content) for m in request.messages),
                )

                response = await adapter.complete(request)

                # Record metrics
                llm_requests_total.labels(
                    provider=p.value,
                    model=response.model,
                    agent_type=request.metadata.get("agent_type", "unknown"),
                ).inc()
                llm_tokens_used_total.labels(
                    provider=p.value, model=response.model, token_type="prompt"
                ).inc(response.prompt_tokens)
                llm_tokens_used_total.labels(
                    provider=p.value, model=response.model, token_type="completion"
                ).inc(response.completion_tokens)

                logger.debug(
                    "llm_request_success",
                    provider=p.value,
                    model=response.model,
                    tokens=response.total_tokens,
                )

                return response

            except LLMRateLimitError as exc:
                logger.warning("llm_rate_limit", provider=p.value, error=str(exc))
                llm_errors_total.labels(provider=p.value, error_type="rate_limit").inc()
                last_error = exc
                # Rate limits → try next provider immediately
                continue

            except LLMTimeoutError as exc:
                logger.warning("llm_timeout", provider=p.value, error=str(exc))
                llm_errors_total.labels(provider=p.value, error_type="timeout").inc()
                last_error = exc
                continue

            except LLMError as exc:
                logger.error("llm_error", provider=p.value, error=str(exc))
                llm_errors_total.labels(provider=p.value, error_type="error").inc()
                last_error = exc
                continue

        # All providers failed
        msg = f"All LLM providers failed. Last error: {last_error}"
        raise LLMError(msg, provider="all")

    def _build_provider_list(
        self,
        primary: LLMProvider | None,
        fallbacks: list[LLMProvider] | None,
    ) -> list[LLMProvider]:
        """Build ordered list of providers to attempt."""
        providers: list[LLMProvider] = []
        if primary:
            providers.append(primary)
        if fallbacks:
            providers.extend(p for p in fallbacks if p not in providers)
        if not providers:
            # Default order based on availability
            default_order = [
                LLMProvider.OPENAI,
                LLMProvider.ANTHROPIC,
                LLMProvider.GOOGLE,
                LLMProvider.OPENROUTER,
                LLMProvider.OLLAMA,
            ]
            providers = [p for p in default_order if p in self._adapters]
        return providers

    async def check_providers_health(self) -> dict[str, bool]:
        """Check health of all configured providers."""
        results: dict[str, bool] = {}
        for provider, adapter in self._adapters.items():
            try:
                results[provider.value] = await adapter.health_check()
            except Exception:
                results[provider.value] = False
        return results
