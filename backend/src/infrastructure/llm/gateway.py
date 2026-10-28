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
import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.infrastructure.llm.usage_store import LLMUsageStore, default_usage_store
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import (
    LLMError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.shared.logging.setup import get_logger
from src.shared.observability.metrics import (
    llm_errors_total,
    llm_requests_total,
    llm_tokens_used_total,
)

logger = get_logger(__name__)

# A rate limit may be per-minute or per-day: honour the provider's retry-after
# when it sends one, otherwise park the provider for a short while so the next
# analysis does not waste a request on the same failing endpoint.
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 300
DEFAULT_QUOTA_COOLDOWN_SECONDS = 1800


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


@dataclass
class _ProviderCooldown:
    """Temporary park for a provider that just failed in a non-transient way."""

    until: float
    reason: str
    until_utc: datetime


def _bucket_to_dict(bucket: _UsageBucket) -> dict[str, Any]:
    return {
        "requests": bucket.requests,
        "failures": bucket.failures,
        "prompt_tokens": bucket.prompt_tokens,
        "completion_tokens": bucket.completion_tokens,
        "latency_ms": bucket.latency_ms,
        "models": sorted(bucket.models),
    }


def _bucket_from_dict(raw: Any) -> _UsageBucket:
    data = dict(raw or {})
    return _UsageBucket(
        requests=int(data.get("requests") or 0),
        failures=int(data.get("failures") or 0),
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
        latency_ms=float(data.get("latency_ms") or 0.0),
        models={str(model) for model in data.get("models") or []},
    )


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


@dataclass(frozen=True)
class _Route:
    """One attempt in the routing chain."""

    provider: LLMProvider
    model: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider.value}:{self.model}" if self.model else self.provider.value


class LLMGateway:
    """
    Routes LLM requests to the appropriate provider.

    Acts as the single point of entry for all LLM interactions in the system.
    Handles provider selection, fallback, metrics, and audit logging.
    """

    def __init__(
        self,
        adapters: dict[LLMProvider, BaseLLMAdapter],
        usage_store: LLMUsageStore | None = None,
    ) -> None:
        self._adapters = adapters
        self._usage_store = usage_store or default_usage_store()
        self._settings = get_settings()
        # A provider SDK without its own timeout can hang the whole cycle.
        # Bound every call here so the gateway can cancel it and try the next
        # provider instead of leaving the autonomous loop blocked.
        self._request_timeout_seconds = float(self._settings.llm_request_timeout_seconds)
        self._usage_date: date = datetime.now(tz=UTC).date()
        self._tokens_used_today = 0
        self._requests_today = 0
        self._request_attempts_today = 0
        self._prompt_tokens_today = 0
        self._completion_tokens_today = 0
        self._fallbacks_today = 0
        self._cycle_tokens_used = 0
        self._cycle_requests_used = 0
        self._by_agent: dict[str, _UsageBucket] = {}
        self._by_provider: dict[str, _UsageBucket] = {}
        # Providers parked after a quota/auth/rate-limit failure. Cooldowns are
        # time-based and deliberately survive begin_cycle(): starting a new
        # analysis does not fix an exhausted account.
        self._cooldowns: dict[str, _ProviderCooldown] = {}
        # Restore today's counters and live cooldowns so a restart or a gateway
        # rebuilt after a settings change cannot bypass the daily budget.
        self._restore_state()

    # ------------------------------------------------------------------
    # Durable state
    # ------------------------------------------------------------------
    def _state_snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "usage_date": self._usage_date.isoformat(),
            "tokens_used_today": self._tokens_used_today,
            "requests_today": self._requests_today,
            "request_attempts_today": self._request_attempts_today,
            "prompt_tokens_today": self._prompt_tokens_today,
            "completion_tokens_today": self._completion_tokens_today,
            "fallbacks_today": self._fallbacks_today,
            "by_agent": {key: _bucket_to_dict(value) for key, value in self._by_agent.items()},
            "by_provider": {
                key: _bucket_to_dict(value) for key, value in self._by_provider.items()
            },
            "cooldowns": {
                provider: {"until_utc": cooldown.until_utc.isoformat(), "reason": cooldown.reason}
                for provider, cooldown in self._cooldowns.items()
            },
        }

    def _persist_state(self) -> None:
        self._usage_store.save(self._state_snapshot())

    def _restore_state(self) -> None:
        try:
            state = self._usage_store.load()
        except Exception as exc:
            logger.warning("llm_usage_state_restore_failed", error=str(exc))
            return
        if not state:
            return
        now_utc = datetime.now(tz=UTC)
        try:
            for provider, raw in dict(state.get("cooldowns") or {}).items():
                until_utc = datetime.fromisoformat(str(raw["until_utc"]))
                remaining = (until_utc - now_utc).total_seconds()
                if remaining > 0:
                    self._cooldowns[str(provider)] = _ProviderCooldown(
                        until=time.monotonic() + remaining,
                        reason=str(raw.get("reason") or "restored"),
                        until_utc=until_utc,
                    )
            # Daily counters only apply to the UTC day they were recorded on.
            if date.fromisoformat(str(state.get("usage_date"))) != self._usage_date:
                return
            self._tokens_used_today = int(state.get("tokens_used_today") or 0)
            self._requests_today = int(state.get("requests_today") or 0)
            self._request_attempts_today = int(state.get("request_attempts_today") or 0)
            self._prompt_tokens_today = int(state.get("prompt_tokens_today") or 0)
            self._completion_tokens_today = int(state.get("completion_tokens_today") or 0)
            self._fallbacks_today = int(state.get("fallbacks_today") or 0)
            self._by_agent = {
                str(key): _bucket_from_dict(value)
                for key, value in dict(state.get("by_agent") or {}).items()
            }
            self._by_provider = {
                str(key): _bucket_from_dict(value)
                for key, value in dict(state.get("by_provider") or {}).items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("llm_usage_state_invalid", error=str(exc))

    def begin_cycle(self) -> None:
        """Reset the per-cycle counters before a new analysis cycle.

        The daily counters are never reset here: they only roll over at UTC
        midnight, so a caller cannot bypass the daily cap by starting cycles.
        Provider cooldowns are also preserved for the same reason.
        """
        self._refresh_usage_day()
        self._cycle_tokens_used = 0
        self._cycle_requests_used = 0

    def _active_cooldown(self, key: str) -> _ProviderCooldown | None:
        """Return the cooldown for a provider (or provider:model), dropping it once expired."""
        cooldown = self._cooldowns.get(key)
        if cooldown is None:
            return None
        if cooldown.until <= time.monotonic():
            self._cooldowns.pop(key, None)
            return None
        return cooldown

    def _start_cooldown(self, provider: str, seconds: int, reason: str) -> None:
        """Park a provider so later cycles skip it instead of burning attempts."""
        if seconds <= 0:
            return
        self._cooldowns[provider] = _ProviderCooldown(
            until=time.monotonic() + seconds,
            reason=reason,
            until_utc=datetime.now(tz=UTC) + timedelta(seconds=seconds),
        )

    def _cooldown_status(self) -> list[dict[str, object]]:
        """Serialize active cooldowns for the status/UI endpoints."""
        now = time.monotonic()
        statuses: list[dict[str, object]] = []
        for provider in sorted(self._cooldowns):
            cooldown = self._cooldowns[provider]
            remaining = cooldown.until - now
            if remaining <= 0:
                continue
            statuses.append(
                {
                    "provider": provider,
                    "reason": cooldown.reason,
                    "seconds_remaining": int(remaining) + 1,
                    "until_utc": cooldown.until_utc.isoformat(),
                }
            )
        return statuses

    @property
    def provider_cooldowns(self) -> list[dict[str, object]]:
        """Providers currently parked after a non-transient failure."""
        return self._cooldown_status()

    def _refresh_usage_day(self) -> None:
        today = datetime.now(tz=UTC).date()
        if self._usage_date != today:
            self._usage_date = today
            self._tokens_used_today = 0
            self._requests_today = 0
            self._request_attempts_today = 0
            self._prompt_tokens_today = 0
            self._completion_tokens_today = 0
            self._fallbacks_today = 0
            self._cycle_tokens_used = 0
            self._cycle_requests_used = 0
            self._by_agent.clear()
            self._by_provider.clear()

    def usage_status(self) -> dict[str, object]:
        """Expose in-memory token usage, per-agent/provider attribution and cost estimate."""
        self._refresh_usage_day()
        budget = int(self._settings.llm_daily_token_budget)
        remaining = max(0, budget - self._tokens_used_today) if budget > 0 else None
        request_budget = int(self._settings.llm_daily_request_budget)
        requests_remaining = (
            max(0, request_budget - self._request_attempts_today) if request_budget > 0 else None
        )
        cycle_budget = int(self._settings.llm_cycle_token_budget)
        cycle_remaining = (
            max(0, cycle_budget - self._cycle_tokens_used) if cycle_budget > 0 else None
        )
        cycle_request_budget = int(self._settings.llm_cycle_request_budget)
        cycle_requests_remaining = (
            max(0, cycle_request_budget - self._cycle_requests_used)
            if cycle_request_budget > 0
            else None
        )
        input_rate = self._settings.llm_input_cost_per_million_usd
        output_rate = self._settings.llm_output_cost_per_million_usd
        rates_configured = input_rate > 0 or output_rate > 0
        return {
            "date_utc": self._usage_date.isoformat(),
            "requests": self._requests_today,
            "request_attempts": self._request_attempts_today,
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
            "daily_request_budget": request_budget,
            "requests_remaining": requests_remaining,
            "request_budget_exhausted": (
                request_budget > 0 and self._request_attempts_today >= request_budget
            ),
            "cycle_token_budget": cycle_budget,
            "cycle_tokens_used": self._cycle_tokens_used,
            "cycle_tokens_remaining": cycle_remaining,
            "cycle_budget_exhausted": (
                cycle_budget > 0 and self._cycle_tokens_used >= cycle_budget
            ),
            "cycle_request_budget": cycle_request_budget,
            "cycle_requests_used": self._cycle_requests_used,
            "cycle_requests_remaining": cycle_requests_remaining,
            "cycle_request_budget_exhausted": (
                cycle_request_budget > 0 and self._cycle_requests_used >= cycle_request_budget
            ),
            "fallbacks": self._fallbacks_today,
            "provider_cooldowns": self.provider_cooldowns,
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
            "durability": "persisted_daily_counters",
        }

    def _enforce_budget(self) -> None:
        self._refresh_usage_day()
        cycle_request_budget = int(self._settings.llm_cycle_request_budget)
        if cycle_request_budget > 0 and self._cycle_requests_used >= cycle_request_budget:
            raise LLMError(
                "Per-cycle LLM request budget exhausted "
                f"({self._cycle_requests_used}/{cycle_request_budget} attempts).",
                provider="budget",
            )
        cycle_budget = int(self._settings.llm_cycle_token_budget)
        if cycle_budget > 0 and self._cycle_tokens_used >= cycle_budget:
            raise LLMError(
                "Per-cycle LLM token budget exhausted "
                f"({self._cycle_tokens_used}/{cycle_budget} tokens).",
                provider="budget",
            )
        request_budget = int(self._settings.llm_daily_request_budget)
        if request_budget > 0 and self._request_attempts_today >= request_budget:
            raise LLMError(
                "Daily LLM request budget exhausted "
                f"({self._request_attempts_today}/{request_budget} attempts).",
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

    def _provider_chain(self) -> list[LLMProvider]:
        """Configured routing order, skipping providers without credentials.

        The requested primary may be missing while its key is not set yet; the
        first configured fallback then becomes the effective primary, so status
        endpoints describe what will actually be attempted.
        """
        chain_entries = [entry for entry in self._settings.llm_model_chain if entry.enabled]
        if chain_entries:
            ordered = list(dict.fromkeys(entry.provider for entry in chain_entries))
            return [provider for provider in ordered if provider in self._adapters]
        requested = self._settings.llm_primary_provider
        chain = [requested]
        if self._settings.llm_fallback_enabled:
            chain.extend(
                provider
                for provider in self._settings.llm_fallback_providers
                if provider != requested
            )
        return [provider for provider in chain if provider in self._adapters]

    @property
    def primary_provider(self) -> str:
        chain = self._provider_chain()
        if chain:
            return chain[0].value
        return self._settings.llm_primary_provider.value

    @property
    def fallback_providers(self) -> list[str]:
        if not self._settings.llm_fallback_enabled:
            return []
        return [provider.value for provider in self._provider_chain()[1:]]

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
        try:
            return await self._complete(request, provider, fallback_providers)
        finally:
            # Every attempt, token and cooldown is persisted, success or not.
            self._persist_state()

    async def _complete(
        self,
        request: LLMRequest,
        provider: LLMProvider | None,
        fallback_providers: list[LLMProvider] | None,
    ) -> LLMResponse:
        primary = provider or self._settings.llm_primary_provider
        routes = self._routes(provider, fallback_providers)
        if not routes:
            if not self._adapters:
                raise LLMError("No LLM providers are configured.", provider="none")
            cooldowns = self._cooldown_status()
            if cooldowns:
                detail = ", ".join(
                    f"{item['provider']} ({item['reason']}, {item['seconds_remaining']}s left)"
                    for item in cooldowns
                )
                raise LLMError(
                    "Every configured LLM provider is cooling down after a recent failure: "
                    f"{detail}.",
                    provider="cooldown",
                )
            raise LLMError(
                "No configured LLM provider matches the requested routing.",
                provider="none",
            )
        self._enforce_budget()

        last_error: Exception | None = None
        agent_type = str(request.metadata.get("agent_type", "unknown"))

        for index, route in enumerate(routes):
            p = route.provider
            adapter = self._adapters.get(p)
            if adapter is None:
                logger.warning("llm_provider_not_configured", provider=p.value)
                continue

            # Re-check the caps before every attempt: one call can fall back
            # across several providers, and free tiers usually limit requests
            # per day rather than tokens.
            try:
                self._enforce_budget()
            except LLMError as exc:
                logger.warning("llm_budget_exhausted", provider=p.value, error=str(exc))
                last_error = exc
                break
            self._request_attempts_today += 1
            self._cycle_requests_used += 1

            try:
                if index > 0:
                    self._fallbacks_today += 1
                    logger.warning(
                        "llm_fallback_attempt",
                        failed_provider=routes[index - 1].key,
                        fallback_provider=route.key,
                    )
                logger.debug(
                    "llm_request_start",
                    provider=p.value,
                    model=request.model,
                    prompt_length=sum(len(m.content) for m in request.messages),
                )

                provider_request = request
                # A model id is provider-specific. A chain entry pins its own
                # model; legacy fallbacks use their configured default instead
                # of receiving (for example) an OpenAI model name.
                if route.model or (p != primary and request.model):
                    provider_request = LLMRequest(
                        messages=request.messages,
                        model=route.model,
                        max_tokens=request.max_tokens,
                        temperature=request.temperature,
                        system_prompt=request.system_prompt,
                        response_format=request.response_format,
                        metadata=request.metadata,
                    )
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(self._request_timeout_seconds):
                        response = await adapter.complete(provider_request)
                except TimeoutError as exc:
                    raise LLMTimeoutError(
                        f"{p.value} exceeded the {self._request_timeout_seconds:.0f}s request cap",
                        provider=p.value,
                    ) from exc
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
                # Free tiers limit each model separately: park only this model
                # so the next entry of the same provider can still answer.
                self._start_cooldown(
                    route.key,
                    exc.retry_after_seconds or DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
                    "rate_limit",
                )
                last_error = exc
                # Rate limits → try next provider immediately
                continue

            except LLMTimeoutError as exc:
                logger.warning("llm_timeout", provider=p.value, error=str(exc))
                self._record_failure(provider=p.value, agent_type=agent_type)
                llm_errors_total.labels(provider=p.value, error_type="timeout").inc()
                last_error = exc
                continue

            except LLMQuotaError as exc:
                # No balance or rejected credentials: retrying every cycle only
                # burns budget, so park the provider for a longer period.
                logger.warning("llm_quota_error", provider=p.value, error=str(exc))
                self._record_failure(provider=p.value, agent_type=agent_type)
                llm_errors_total.labels(provider=p.value, error_type="quota").inc()
                self._start_cooldown(
                    p.value,
                    exc.cooldown_seconds or DEFAULT_QUOTA_COOLDOWN_SECONDS,
                    "quota",
                )
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

    def _routes(
        self,
        provider: LLMProvider | None,
        fallback_providers: list[LLMProvider] | None,
    ) -> list[_Route]:
        """Ordered (provider, model) attempts for one request.

        The UI-managed model chain wins unless the caller pins a provider.
        Entries are skipped while their provider (quota/auth) or the model
        itself (rate limit) is cooling down.
        """
        chain = [entry for entry in self._settings.llm_model_chain if entry.enabled]
        if provider is None and fallback_providers is None and chain:
            routes: list[_Route] = []
            for entry in chain:
                route = _Route(entry.provider, entry.model.strip())
                if route in routes:
                    continue
                if entry.provider not in self._adapters:
                    logger.info("llm_chain_entry_not_configured", route=route.key)
                    continue
                cooldown = self._active_cooldown(entry.provider.value) or self._active_cooldown(
                    route.key
                )
                if cooldown is not None:
                    logger.info(
                        "llm_provider_cooling_down",
                        provider=route.key,
                        reason=cooldown.reason,
                        seconds_remaining=int(cooldown.until - time.monotonic()) + 1,
                    )
                    continue
                routes.append(route)
            return routes

        primary = provider or self._settings.llm_primary_provider
        fallbacks = fallback_providers
        if fallbacks is None and self._settings.llm_fallback_enabled:
            fallbacks = self._settings.llm_fallback_providers
        return [_Route(item, "") for item in self._build_provider_list(primary, fallbacks)]

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
                LLMProvider.DEEPSEEK,
                LLMProvider.OLLAMA,
            ]
            providers = [p for p in default_order if p in self._adapters]

        available: list[LLMProvider] = []
        for provider in providers:
            if provider not in self._adapters:
                logger.warning("llm_provider_not_configured", provider=provider.value)
                continue
            cooldown = self._active_cooldown(provider.value)
            if cooldown is not None:
                logger.info(
                    "llm_provider_cooling_down",
                    provider=provider.value,
                    reason=cooldown.reason,
                    seconds_remaining=int(cooldown.until - time.monotonic()) + 1,
                )
                continue
            available.append(provider)
        return available

    async def check_providers_health(self) -> dict[str, bool]:
        """Check health of all configured providers."""
        results: dict[str, bool] = {}
        for provider, adapter in self._adapters.items():
            try:
                results[provider.value] = await adapter.health_check()
            except Exception:
                results[provider.value] = False
        return results
