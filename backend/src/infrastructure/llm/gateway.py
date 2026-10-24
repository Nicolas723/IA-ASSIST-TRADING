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
import json
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
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


@dataclass
class _UsageBucket:
    """In-memory usage counters for one agent or one provider."""

    requests: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    models: set[str] = field(default_factory=set)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> None:
        self.requests += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.latency_ms += latency_ms
        self.models.add(model)

    def as_dict(self, *, input_rate: Decimal, output_rate: Decimal) -> dict[str, object]:
        requests = self.requests
        cost = _estimate_cost(self.prompt_tokens, self.completion_tokens, input_rate, output_rate)
        return {
            "requests": requests,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "avg_tokens_per_request": (
                round(self.total_tokens / requests, 2) if requests else None
            ),
            "avg_latency_ms": (round(self.latency_ms / requests, 2) if requests else None),
            "estimated_cost_usd": cost,
            "models": sorted(self.models),
        }


def _estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_rate: Decimal,
    output_rate: Decimal,
) -> float | None:
    """Estimate USD from configured per-million rates.

    Returns ``None`` when no rate is configured, so callers never confuse an
    unknown price with a zero cost.
    """
    if input_rate == 0 and output_rate == 0:
        return None
    amount = (
        Decimal(prompt_tokens) * input_rate + Decimal(completion_tokens) * output_rate
    ) / Decimal(1_000_000)
    return round(float(amount), 6)


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
        self._usage_date: date = datetime.now(tz=UTC).date()
        self._tokens_used_today = 0
        self._requests_today = 0
        self._prompt_tokens_today = 0
        self._completion_tokens_today = 0
        self._fallbacks_today = 0
        self._cycle_tokens_used = 0
        self._by_agent: dict[str, _UsageBucket] = {}
        self._by_provider: dict[str, _UsageBucket] = {}

    def begin_cycle(self) -> None:
        """Reset the per-cycle token counter before a new analysis cycle.

        The daily counter is never reset here: it only rolls over at UTC
        midnight, so a caller cannot bypass the daily cap by starting cycles.
        """
        self._refresh_usage_day()
        self._cycle_tokens_used = 0

    def _refresh_usage_day(self) -> None:
        today = datetime.now(tz=UTC).date()
        if self._usage_date != today:
            self._usage_date = today
            self._tokens_used_today = 0
            self._requests_today = 0
            self._prompt_tokens_today = 0
            self._completion_tokens_today = 0
            self._fallbacks_today = 0
            self._cycle_tokens_used = 0
            self._by_agent.clear()
            self._by_provider.clear()

    def usage_status(self) -> dict[str, object]:
        """Expose in-memory token usage, per-agent/provider attribution and cost estimate."""
        self._refresh_usage_day()
        budget = int(self._settings.llm_daily_token_budget)
        remaining = max(0, budget - self._tokens_used_today) if budget > 0 else None
        cycle_budget = int(self._settings.llm_cycle_token_budget)
        cycle_remaining = (
            max(0, cycle_budget - self._cycle_tokens_used) if cycle_budget > 0 else None
        )
        input_rate = self._settings.llm_input_cost_per_million_usd
        output_rate = self._settings.llm_output_cost_per_million_usd
        rates_configured = input_rate > 0 or output_rate > 0
        return {
            "date_utc": self._usage_date.isoformat(),
            "requests": self._requests_today,
            "tokens_used": self._tokens_used_today,
            "prompt_tokens": self._prompt_tokens_today,
            "completion_tokens": self._completion_tokens_today,
            "avg_tokens_per_request": (
                round(self._tokens_used_today / self._requests_today, 2)
                if self._requests_today
                else None
            ),
            "daily_token_budget": budget,
            "tokens_remaining": remaining,
            "budget_exhausted": budget > 0 and self._tokens_used_today >= budget,
            "cycle_token_budget": cycle_budget,
            "cycle_tokens_used": self._cycle_tokens_used,
            "cycle_tokens_remaining": cycle_remaining,
            "cycle_budget_exhausted": (
                cycle_budget > 0 and self._cycle_tokens_used >= cycle_budget
            ),
            "fallbacks": self._fallbacks_today,
            "estimated_cost_usd": _estimate_cost(
                self._prompt_tokens_today,
                self._completion_tokens_today,
                input_rate,
                output_rate,
            ),
            "pricing": {
                "input_usd_per_million_tokens": float(input_rate),
                "output_usd_per_million_tokens": float(output_rate),
                "configured": rates_configured,
            },
            "by_agent": {
                agent: bucket.as_dict(input_rate=input_rate, output_rate=output_rate)
                for agent, bucket in sorted(self._by_agent.items())
            },
            "by_provider": {
                provider: bucket.as_dict(input_rate=input_rate, output_rate=output_rate)
                for provider, bucket in sorted(self._by_provider.items())
            },
            "cost_estimate_note": (
                "Estimated from configured per-million-token rates. "
                "None means no rate is configured, not that the call was free."
            ),
            "durability": "in_memory_process_only",
        }

    def _enforce_budget(self) -> None:
        self._refresh_usage_day()
        cycle_budget = int(self._settings.llm_cycle_token_budget)
        if cycle_budget > 0 and self._cycle_tokens_used >= cycle_budget:
            raise LLMError(
                "Per-cycle LLM token budget exhausted "
                f"({self._cycle_tokens_used}/{cycle_budget} tokens).",
                provider="budget",
            )
        budget = int(self._settings.llm_daily_token_budget)
        if budget > 0 and self._tokens_used_today >= budget:
            raise LLMError(
                f"Daily LLM token budget exhausted ({self._tokens_used_today}/{budget} tokens).",
                provider="budget",
            )

    def _record_usage(
        self,
        response: LLMResponse,
        *,
        provider: str,
        agent_type: str,
        latency_ms: float,
    ) -> None:
        self._refresh_usage_day()
        prompt_tokens = response.prompt_tokens
        completion_tokens = response.completion_tokens
        total = response.total_tokens or (prompt_tokens + completion_tokens)
        self._tokens_used_today += total
        self._cycle_tokens_used += total
        self._requests_today += 1
        self._prompt_tokens_today += prompt_tokens
        self._completion_tokens_today += completion_tokens
        self._bucket(self._by_agent, agent_type).record(
            model=response.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        self._bucket(self._by_provider, provider).record(
            model=response.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

    def _record_failure(self, *, provider: str, agent_type: str) -> None:
        self._refresh_usage_day()
        self._bucket(self._by_agent, agent_type).failures += 1
        self._bucket(self._by_provider, provider).failures += 1

    @staticmethod
    def _bucket(mapping: dict[str, _UsageBucket], key: str) -> _UsageBucket:
        bucket = mapping.get(key)
        if bucket is None:
            bucket = _UsageBucket()
            mapping[key] = bucket
        return bucket

    @property
    def configured_providers(self) -> list[str]:
        """Provider names with credentials/adapters configured (never secrets)."""
        return sorted(provider.value for provider in self._adapters)

    @property
    def primary_provider(self) -> str:
        return self._settings.llm_primary_provider.value

    @property
    def fallback_providers(self) -> list[str]:
        if not self._settings.llm_fallback_enabled:
            return []
        return [provider.value for provider in self._settings.llm_fallback_providers]

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
        primary = provider or self._settings.llm_primary_provider
        fallbacks = fallback_providers
        if fallbacks is None and self._settings.llm_fallback_enabled:
            fallbacks = self._settings.llm_fallback_providers
        providers_to_try = self._build_provider_list(primary, fallbacks)
        self._enforce_budget()

        last_error: Exception | None = None
        agent_type = str(request.metadata.get("agent_type", "unknown"))

        for index, p in enumerate(providers_to_try):
            adapter = self._adapters.get(p)
            if adapter is None:
                logger.warning("llm_provider_not_configured", provider=p.value)
                continue

            try:
                if index > 0:
                    self._fallbacks_today += 1
                    logger.warning(
                        "llm_fallback_attempt",
                        failed_provider=providers_to_try[index - 1].value,
                        fallback_provider=p.value,
                    )
                logger.debug(
                    "llm_request_start",
                    provider=p.value,
                    model=request.model,
                    prompt_length=sum(len(m.content) for m in request.messages),
                )

                provider_request = request
                # A model id is provider-specific. Fallback adapters use their
                # own configured default model instead of receiving (for
                # example) an OpenAI model name.
                if p != primary and request.model:
                    provider_request = LLMRequest(
                        messages=request.messages,
                        model="",
                        max_tokens=request.max_tokens,
                        temperature=request.temperature,
                        system_prompt=request.system_prompt,
                        response_format=request.response_format,
                        metadata=request.metadata,
                    )
                started = time.perf_counter()
                response = await adapter.complete(provider_request)
                latency_ms = (time.perf_counter() - started) * 1000.0
                # Tokens are spent once the provider answers, even if the
                # payload later fails schema validation.
                self._record_usage(
                    response,
                    provider=p.value,
                    agent_type=agent_type,
                    latency_ms=latency_ms,
                )

                if request.response_format == "json":
                    content = response.content.strip()
                    if content.startswith("```"):
                        content = content.removeprefix("```json").removeprefix("```")
                        content = content.removesuffix("```").strip()
                    try:
                        parsed_content = json.loads(content)
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise LLMError(
                            "Provider returned a non-JSON response for a JSON request.",
                            provider=p.value,
                        ) from exc
                    if agent_type in {"technical_analyst", "price_action_analyst"}:
                        valid_signals = {
                            "strong_buy",
                            "buy",
                            "neutral",
                            "sell",
                            "strong_sell",
                        }
                        if (
                            not isinstance(parsed_content, dict)
                            or not {"signal", "confidence", "reasoning"}.issubset(parsed_content)
                            or parsed_content.get("signal") not in valid_signals
                            or not isinstance(parsed_content.get("confidence"), int | float)
                            or not 0.0 <= float(parsed_content["confidence"]) <= 1.0
                            or not isinstance(parsed_content.get("reasoning"), str)
                        ):
                            raise LLMError(
                                "Provider returned JSON that does not match the agent response schema.",
                                provider=p.value,
                            )
                    if content != response.content:
                        response = replace(response, content=content)

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
                self._record_failure(provider=p.value, agent_type=agent_type)
                llm_errors_total.labels(provider=p.value, error_type="rate_limit").inc()
                last_error = exc
                # Rate limits → try next provider immediately
                continue

            except LLMTimeoutError as exc:
                logger.warning("llm_timeout", provider=p.value, error=str(exc))
                self._record_failure(provider=p.value, agent_type=agent_type)
                llm_errors_total.labels(provider=p.value, error_type="timeout").inc()
                last_error = exc
                continue

            except LLMError as exc:
                logger.error("llm_error", provider=p.value, error=str(exc))
                self._record_failure(provider=p.value, agent_type=agent_type)
                llm_errors_total.labels(provider=p.value, error_type="error").inc()
                last_error = exc
                continue

            except Exception as exc:
                # Treat unexpected SDK/provider failures as provider failures
                # too, so an alternate configured model can still respond.
                logger.warning("llm_provider_unexpected_error", provider=p.value, error=str(exc))
                self._record_failure(provider=p.value, agent_type=agent_type)
                llm_errors_total.labels(provider=p.value, error_type="unexpected").inc()
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
