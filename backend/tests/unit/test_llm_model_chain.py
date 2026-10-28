"""Ordered provider+model chain, per-model rate limits and the settings bugs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from src.application.services.integration_settings_service import (
    IntegrationSettingsService,
    LLMProviderUpdate,
    LLMSettingsUpdate,
)
from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMGateway, LLMRequest, LLMResponse
from src.shared.config.settings import LLMChainEntry, LLMProvider, Settings
from src.shared.exceptions.domain import LLMRateLimitError

if TYPE_CHECKING:
    from pathlib import Path


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        return self.data

    def save(self, state: dict[str, Any]) -> None:
        self.data = state


class _Adapter(BaseLLMAdapter):
    def __init__(self, provider: LLMProvider, limited: set[str] | None = None) -> None:
        self._provider = provider
        self.limited = limited or set()
        self.models: list[str] = []

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.models.append(request.model)
        if request.model in self.limited:
            raise LLMRateLimitError("429", provider=self._provider.value)
        return LLMResponse(
            content="ok",
            model=request.model or "default",
            provider=self._provider,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    async def health_check(self) -> bool:
        return True


def _gateway(chain: list[LLMChainEntry], adapters: list[_Adapter]) -> LLMGateway:
    gateway = LLMGateway({a.provider: a for a in adapters}, usage_store=_Store())  # type: ignore[arg-type]
    gateway._settings = Settings(_env_file=None, llm_model_chain=chain)
    return gateway


def _request() -> LLMRequest:
    return LLMRequest(messages=[], model="", metadata={"agent_type": "technical_analyst"})


async def test_chain_order_and_models_are_followed() -> None:
    groq = _Adapter(LLMProvider.GROQ, limited={"openai/gpt-oss-120b"})
    router = _Adapter(LLMProvider.OPENROUTER)
    gateway = _gateway(
        [
            LLMChainEntry(provider=LLMProvider.GROQ, model="openai/gpt-oss-120b"),
            LLMChainEntry(provider=LLMProvider.GROQ, model="llama-3.3-70b-versatile"),
            LLMChainEntry(provider=LLMProvider.OPENROUTER, model="x/free:free"),
        ],
        [groq, router],
    )

    response = await gateway.complete(_request())

    # The first Groq model is rate limited; the second model of the same
    # provider answers before falling through to OpenRouter.
    assert response.model == "llama-3.3-70b-versatile"
    assert groq.models == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    assert router.models == []


async def test_rate_limited_model_is_skipped_but_provider_stays_usable() -> None:
    groq = _Adapter(LLMProvider.GROQ, limited={"a"})
    gateway = _gateway(
        [
            LLMChainEntry(provider=LLMProvider.GROQ, model="a"),
            LLMChainEntry(provider=LLMProvider.GROQ, model="b"),
        ],
        [groq],
    )
    await gateway.complete(_request())
    await gateway.complete(_request())

    assert groq.models == ["a", "b", "b"]
    assert [c["provider"] for c in gateway.provider_cooldowns] == ["groq:a"]


async def test_disabled_and_unconfigured_entries_are_ignored() -> None:
    router = _Adapter(LLMProvider.OPENROUTER)
    gateway = _gateway(
        [
            LLMChainEntry(provider=LLMProvider.GROQ, model="a"),  # no adapter
            LLMChainEntry(provider=LLMProvider.OPENROUTER, model="off", enabled=False),
            LLMChainEntry(provider=LLMProvider.OPENROUTER, model="on"),
        ],
        [router],
    )
    response = await gateway.complete(_request())
    assert response.model == "on"
    assert gateway.primary_provider == "openrouter"


class _MemoryStore:
    def __init__(self) -> None:
        self.sections: dict[str, dict[str, Any]] = {}

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.sections.get(name) or {})

    def set_section(self, name: str, value: dict[str, Any]) -> None:
        self.sections[name] = value

    def as_dict(self) -> dict[str, Any]:
        return self.sections


def test_saving_every_provider_including_ollama_applies_the_chain(tmp_path: Path) -> None:
    del tmp_path
    settings = Settings(_env_file=None)
    store = _MemoryStore()
    service = IntegrationSettingsService(settings=settings, store=store)  # type: ignore[arg-type]

    # The UI sends every provider row; Ollama has no api_key field. This used
    # to abort the apply and leave OpenRouter and the order unapplied.
    service.update_llm(
        LLMSettingsUpdate(
            providers=[
                LLMProviderUpdate(provider=LLMProvider.OPENROUTER, api_key="sk-or-123456789"),
                LLMProviderUpdate(provider=LLMProvider.OLLAMA, enabled=False),
            ],
            model_chain=[
                LLMChainEntry(provider=LLMProvider.OPENROUTER, model=" a:free "),
                LLMChainEntry(provider=LLMProvider.OPENROUTER, model="a:free"),
                LLMChainEntry(provider=LLMProvider.GROQ),
            ],
        )
    )

    assert settings.openrouter.api_key == "sk-or-123456789"
    assert "api_key" not in store.sections["llm"]["providers"]["ollama"]
    assert [entry.key for entry in settings.llm_model_chain] == ["openrouter:a:free", "groq"]

    # A store written by the old code (empty Ollama key) must still apply.
    store.sections["llm"]["providers"]["ollama"]["api_key"] = ""
    IntegrationSettingsService(settings=settings, store=store).apply_persisted()  # type: ignore[arg-type]


async def test_openrouter_catalogue_lists_free_models_first() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "1"}},
                    {"id": "meta/llama:free", "name": "Llama", "pricing": {"prompt": "0"}},
                    {"id": "z/zero", "pricing": {"prompt": "0", "completion": "0"}},
                ]
            },
        )

    service = IntegrationSettingsService(settings=Settings(_env_file=None), store=_MemoryStore())  # type: ignore[arg-type]
    options = await service.list_models(
        LLMProvider.OPENROUTER, transport=httpx.MockTransport(handler)
    )
    assert [(o.id, o.free) for o in options] == [
        ("meta/llama:free", True),
        ("z/zero", True),
        ("openai/gpt-4o", False),
    ]
