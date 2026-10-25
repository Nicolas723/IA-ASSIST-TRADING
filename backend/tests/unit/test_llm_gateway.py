"""LLM routing honors configured primary/fallback providers safely."""

from __future__ import annotations

import pytest

from src.infrastructure.llm.gateway import (
    BaseLLMAdapter,
    LLMGateway,
    LLMRequest,
    LLMResponse,
)
from src.shared.config.settings import LLMProvider, Settings
from src.shared.exceptions.domain import LLMError


class FakeAdapter(BaseLLMAdapter):
    def __init__(
        self,
        provider: LLMProvider,
        response: LLMResponse | Exception,
    ) -> None:
        self._provider = provider
        self.response = response
        self.requests: list[LLMRequest] = []

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def health_check(self) -> bool:
        return True


def _response(provider: LLMProvider, content: str) -> LLMResponse:
    return LLMResponse(content=content, model="provider-default", provider=provider)


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[LLMProvider.GOOGLE, LLMProvider.ANTHROPIC],
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    return None


@pytest.mark.asyncio
async def test_gateway_uses_fallback_provider_when_primary_fails() -> None:
    primary = FakeAdapter(LLMProvider.OPENAI, LLMError("temporary API error"))
    fallback = FakeAdapter(LLMProvider.GOOGLE, _response(LLMProvider.GOOGLE, '{"signal":"buy"}'))
    gateway = LLMGateway({LLMProvider.OPENAI: primary, LLMProvider.GOOGLE: fallback})

    result = await gateway.complete(
        LLMRequest(messages=[], model="gpt-4o-mini", response_format="json")
    )

    assert result.provider == LLMProvider.GOOGLE
    assert fallback.requests[0].model == ""


@pytest.mark.asyncio
async def test_gateway_falls_back_on_malformed_json() -> None:
    primary = FakeAdapter(LLMProvider.OPENAI, _response(LLMProvider.OPENAI, "not json"))
    fallback = FakeAdapter(
        LLMProvider.ANTHROPIC,
        _response(LLMProvider.ANTHROPIC, '```json\n{"signal":"neutral"}\n```'),
    )
    gateway = LLMGateway({LLMProvider.OPENAI: primary, LLMProvider.ANTHROPIC: fallback})

    result = await gateway.complete(
        LLMRequest(messages=[], model="gpt-4o-mini", response_format="json")
    )

    assert result.provider == LLMProvider.ANTHROPIC
    assert result.content == '{"signal":"neutral"}'


@pytest.mark.asyncio
async def test_gateway_reports_all_provider_failures() -> None:
    primary = FakeAdapter(LLMProvider.OPENAI, LLMError("primary failed"))
    fallback = FakeAdapter(LLMProvider.GOOGLE, LLMError("fallback failed"))
    gateway = LLMGateway({LLMProvider.OPENAI: primary, LLMProvider.GOOGLE: fallback})

    with pytest.raises(LLMError, match="All LLM providers failed"):
        await gateway.complete(LLMRequest(messages=[], model=""))


def test_gateway_exposes_provider_names_without_secrets() -> None:
    gateway = LLMGateway(
        {
            LLMProvider.OPENAI: FakeAdapter(
                LLMProvider.OPENAI,
                _response(LLMProvider.OPENAI, "ok"),
            )
        }
    )

    assert gateway.configured_providers == ["openai"]
    assert gateway.primary_provider == "openai"
    # Unconfigured providers are never usable fallbacks, so they are not reported.
    assert gateway.fallback_providers == []


def test_fallback_providers_exclude_the_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.GOOGLE,
        llm_fallback_providers=[LLMProvider.GOOGLE, LLMProvider.ANTHROPIC],
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    gateway = LLMGateway(
        {
            LLMProvider.GOOGLE: FakeAdapter(
                LLMProvider.GOOGLE, _response(LLMProvider.GOOGLE, "ok")
            ),
            LLMProvider.ANTHROPIC: FakeAdapter(
                LLMProvider.ANTHROPIC, _response(LLMProvider.ANTHROPIC, "ok")
            ),
        }
    )

    assert gateway.fallback_providers == ["anthropic"]


def test_fallback_providers_exclude_providers_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.GOOGLE,
        llm_fallback_providers=[LLMProvider.OPENAI, LLMProvider.ANTHROPIC],
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    gateway = LLMGateway(
        {LLMProvider.GOOGLE: FakeAdapter(LLMProvider.GOOGLE, _response(LLMProvider.GOOGLE, "ok"))}
    )

    assert gateway.fallback_providers == []


@pytest.mark.asyncio
async def test_gateway_blocks_calls_once_daily_token_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[],
        llm_daily_token_budget=100,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    adapter = FakeAdapter(
        LLMProvider.OPENAI,
        LLMResponse(
            content="ok",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            prompt_tokens=60,
            completion_tokens=50,
            total_tokens=110,
        ),
    )
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})

    assert (await gateway.complete(LLMRequest(messages=[], model=""))).content == "ok"
    assert gateway.usage_status()["budget_exhausted"] is True
    assert gateway.usage_status()["tokens_used"] == 110

    with pytest.raises(LLMError, match="budget exhausted"):
        await gateway.complete(LLMRequest(messages=[], model=""))
    assert len(adapter.requests) == 1


@pytest.mark.asyncio
async def test_usage_status_attributes_tokens_and_cost_by_agent_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[],
        llm_input_cost_per_million_usd=1.0,
        llm_output_cost_per_million_usd=2.0,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    adapter = FakeAdapter(
        LLMProvider.OPENAI,
        LLMResponse(
            content="ok",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            total_tokens=1_500_000,
        ),
    )
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})

    await gateway.complete(
        LLMRequest(
            messages=[],
            model="",
            metadata={"agent_type": "technical_analyst"},
        )
    )

    status = gateway.usage_status()
    agent = status["by_agent"]["technical_analyst"]
    provider = status["by_provider"]["openai"]
    assert agent["total_tokens"] == 1_500_000
    assert agent["estimated_cost_usd"] == pytest.approx(2.0)
    assert provider["requests"] == 1
    assert provider["models"] == ["gpt-4o-mini"]
    assert status["estimated_cost_usd"] == pytest.approx(2.0)
    assert status["pricing"]["configured"] is True


@pytest.mark.asyncio
async def test_usage_status_reports_unknown_cost_without_configured_rates() -> None:
    adapter = FakeAdapter(
        LLMProvider.OPENAI,
        LLMResponse(
            content="ok",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})

    await gateway.complete(LLMRequest(messages=[], model=""))

    status = gateway.usage_status()
    assert status["estimated_cost_usd"] is None
    assert status["by_agent"]["unknown"]["estimated_cost_usd"] is None
    assert status["pricing"]["configured"] is False


@pytest.mark.asyncio
async def test_usage_status_counts_failures_and_fallbacks() -> None:
    primary = FakeAdapter(LLMProvider.OPENAI, LLMError("primary failed"))
    fallback = FakeAdapter(LLMProvider.GOOGLE, _response(LLMProvider.GOOGLE, "ok"))
    gateway = LLMGateway({LLMProvider.OPENAI: primary, LLMProvider.GOOGLE: fallback})

    await gateway.complete(
        LLMRequest(messages=[], model="", metadata={"agent_type": "price_action_analyst"})
    )

    status = gateway.usage_status()
    assert status["fallbacks"] == 1
    assert status["by_provider"]["openai"]["failures"] == 1
    assert status["by_provider"]["google"]["requests"] == 1
    assert status["by_agent"]["price_action_analyst"]["failures"] == 1


@pytest.mark.asyncio
async def test_gateway_blocks_calls_once_cycle_token_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[],
        llm_cycle_token_budget=100,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    adapter = FakeAdapter(
        LLMProvider.OPENAI,
        LLMResponse(
            content="ok",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            prompt_tokens=60,
            completion_tokens=50,
            total_tokens=110,
        ),
    )
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})
    gateway.begin_cycle()

    assert (await gateway.complete(LLMRequest(messages=[], model=""))).content == "ok"
    status = gateway.usage_status()
    assert status["cycle_tokens_used"] == 110
    assert status["cycle_budget_exhausted"] is True
    # The daily counter is independent and stays enabled.
    assert status["budget_exhausted"] is False

    with pytest.raises(LLMError, match="Per-cycle LLM token budget exhausted"):
        await gateway.complete(LLMRequest(messages=[], model=""))
    assert len(adapter.requests) == 1


@pytest.mark.asyncio
async def test_begin_cycle_resets_only_the_cycle_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[],
        llm_daily_token_budget=100,
        llm_cycle_token_budget=100,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    adapter = FakeAdapter(
        LLMProvider.OPENAI,
        LLMResponse(
            content="ok",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            prompt_tokens=60,
            completion_tokens=50,
            total_tokens=110,
        ),
    )
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})
    gateway.begin_cycle()

    await gateway.complete(LLMRequest(messages=[], model=""))
    gateway.begin_cycle()

    status = gateway.usage_status()
    assert status["cycle_tokens_used"] == 0
    assert status["tokens_used"] == 110
    # Starting a new cycle must not bypass the daily cap.
    assert status["budget_exhausted"] is True
    with pytest.raises(LLMError, match="Daily LLM token budget exhausted"):
        await gateway.complete(LLMRequest(messages=[], model=""))
    assert len(adapter.requests) == 1


@pytest.mark.asyncio
async def test_gateway_blocks_calls_once_daily_request_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[],
        llm_daily_request_budget=2,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    adapter = FakeAdapter(LLMProvider.OPENAI, _response(LLMProvider.OPENAI, "ok"))
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})

    await gateway.complete(LLMRequest(messages=[], model=""))
    await gateway.complete(LLMRequest(messages=[], model=""))

    status = gateway.usage_status()
    assert status["request_attempts"] == 2
    assert status["requests"] == 2
    assert status["daily_request_budget"] == 2
    assert status["requests_remaining"] == 0
    assert status["request_budget_exhausted"] is True

    with pytest.raises(LLMError, match="Daily LLM request budget exhausted"):
        await gateway.complete(LLMRequest(messages=[], model=""))
    assert len(adapter.requests) == 2


@pytest.mark.asyncio
async def test_cycle_request_budget_counts_failed_attempts_and_blocks_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[LLMProvider.GOOGLE],
        llm_cycle_request_budget=1,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    primary = FakeAdapter(LLMProvider.OPENAI, LLMError("primary failed"))
    fallback = FakeAdapter(LLMProvider.GOOGLE, _response(LLMProvider.GOOGLE, "ok"))
    gateway = LLMGateway({LLMProvider.OPENAI: primary, LLMProvider.GOOGLE: fallback})
    gateway.begin_cycle()

    with pytest.raises(LLMError, match="Per-cycle LLM request budget exhausted"):
        await gateway.complete(LLMRequest(messages=[], model=""))

    # The failed primary attempt already spent the only request of the cycle,
    # so the fallback must not reach the network.
    assert len(primary.requests) == 1
    assert len(fallback.requests) == 0
    status = gateway.usage_status()
    assert status["cycle_requests_used"] == 1
    assert status["cycle_request_budget_exhausted"] is True
    assert status["request_attempts"] == 1


@pytest.mark.asyncio
async def test_begin_cycle_resets_the_request_counter_only_for_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure.llm import gateway as gateway_module

    config = Settings(
        _env_file=None,
        llm_primary_provider=LLMProvider.OPENAI,
        llm_fallback_providers=[],
        llm_daily_request_budget=1,
        llm_cycle_request_budget=1,
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: config)
    adapter = FakeAdapter(LLMProvider.OPENAI, _response(LLMProvider.OPENAI, "ok"))
    gateway = LLMGateway({LLMProvider.OPENAI: adapter})
    gateway.begin_cycle()

    await gateway.complete(LLMRequest(messages=[], model=""))
    gateway.begin_cycle()

    status = gateway.usage_status()
    assert status["cycle_requests_used"] == 0
    assert status["request_attempts"] == 1

    # Starting a new cycle resets the cycle counter but must not bypass the
    # daily cap.
    with pytest.raises(LLMError, match="Daily LLM request budget exhausted"):
        await gateway.complete(LLMRequest(messages=[], model=""))
    assert len(adapter.requests) == 1
