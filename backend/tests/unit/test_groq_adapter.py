"""Unit tests for the Groq adapter and its factory wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.llm import factory as factory_module
from src.infrastructure.llm import gateway as gateway_module
from src.infrastructure.llm import groq_adapter as groq_module
from src.infrastructure.llm import openai_adapter as openai_module
from src.infrastructure.llm.gateway import LLMMessage, LLMRequest
from src.infrastructure.llm.groq_adapter import GroqAdapter
from src.shared.config.settings import (
    AnthropicSettings,
    DeepSeekSettings,
    GeminiSettings,
    GroqSettings,
    LLMProvider,
    OllamaSettings,
    OpenAISettings,
    OpenRouterSettings,
    Settings,
)


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="groq answer"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            model=kwargs["model"],
            model_dump=lambda: {"id": "fake"},
        )


class FakeAsyncOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=FakeCompletions())


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        openai=SimpleNamespace(api_key="", default_model="gpt-4o-mini"),
        groq=SimpleNamespace(
            api_key="groq-key",
            base_url="https://api.groq.com/openai/v1",
            default_model="openai/gpt-oss-120b",
        ),
    )


def _build_adapter(monkeypatch: pytest.MonkeyPatch) -> GroqAdapter:
    config = _config()
    monkeypatch.setattr(groq_module, "get_settings", lambda: config)
    monkeypatch.setattr(openai_module, "get_settings", lambda: config)
    monkeypatch.setattr(openai_module, "AsyncOpenAI", FakeAsyncOpenAI)
    return GroqAdapter()


def test_adapter_uses_groq_credentials_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)

    assert adapter.provider is LLMProvider.GROQ
    assert adapter._client.kwargs["api_key"] == "groq-key"
    assert adapter._client.kwargs["base_url"] == "https://api.groq.com/openai/v1"
    assert adapter._default_model == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_complete_labels_the_groq_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)

    result = await adapter.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="hi")], model="")
    )

    assert result.content == "groq answer"
    assert result.provider is LLMProvider.GROQ
    assert result.model == "openai/gpt-oss-120b"
    assert result.total_tokens == 8


def test_factory_registers_groq_when_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        openai=OpenAISettings(_env_file=None, api_key=""),
        anthropic=AnthropicSettings(_env_file=None, api_key=""),
        gemini=GeminiSettings(_env_file=None, api_key=""),
        openrouter=OpenRouterSettings(_env_file=None, api_key=""),
        ollama=OllamaSettings(_env_file=None, enabled=False),
        deepseek=DeepSeekSettings(_env_file=None, api_key=""),
        groq=GroqSettings(_env_file=None, api_key="groq-key"),
    )
    fake_adapter = SimpleNamespace(provider=LLMProvider.GROQ)
    monkeypatch.setattr(groq_module, "GroqAdapter", lambda: fake_adapter)

    gateway = factory_module.create_llm_gateway(settings)

    assert gateway is not None
    assert gateway.configured_providers == ["groq"]


def test_groq_can_be_the_primary_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        openai=OpenAISettings(_env_file=None, api_key=""),
        anthropic=AnthropicSettings(_env_file=None, api_key=""),
        gemini=GeminiSettings(_env_file=None, api_key=""),
        openrouter=OpenRouterSettings(_env_file=None, api_key=""),
        ollama=OllamaSettings(_env_file=None, enabled=False),
        deepseek=DeepSeekSettings(_env_file=None, api_key=""),
        groq=GroqSettings(_env_file=None, api_key="groq-key"),
        llm_primary_provider=LLMProvider.GROQ,
        llm_fallback_providers=[],
    )
    monkeypatch.setattr(
        groq_module,
        "GroqAdapter",
        lambda: SimpleNamespace(provider=LLMProvider.GROQ),
    )
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    gateway = factory_module.create_llm_gateway(settings)

    assert gateway is not None
    assert gateway.primary_provider == "groq"
