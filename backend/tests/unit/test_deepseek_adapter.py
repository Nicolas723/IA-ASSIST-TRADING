"""Unit tests for the DeepSeek adapter and its factory wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.llm import deepseek_adapter as deepseek_module
from src.infrastructure.llm import factory as factory_module
from src.infrastructure.llm import openai_adapter as openai_module
from src.infrastructure.llm.deepseek_adapter import DeepSeekAdapter
from src.infrastructure.llm.gateway import LLMMessage, LLMRequest
from src.shared.config.settings import (
    AnthropicSettings,
    DeepSeekSettings,
    GeminiSettings,
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
                    message=SimpleNamespace(content="deepseek answer"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=4, total_tokens=11),
            model="deepseek-chat",
            model_dump=lambda: {"id": "fake"},
        )


class FakeModels:
    async def list(self) -> list[Any]:
        return []


class FakeAsyncOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=FakeCompletions())
        self.models = FakeModels()


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        openai=SimpleNamespace(api_key="", default_model="gpt-4o-mini"),
        deepseek=SimpleNamespace(
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            default_model="deepseek-chat",
        ),
    )


def _build_adapter(monkeypatch: pytest.MonkeyPatch) -> DeepSeekAdapter:
    config = _config()
    monkeypatch.setattr(deepseek_module, "get_settings", lambda: config)
    monkeypatch.setattr(openai_module, "get_settings", lambda: config)
    monkeypatch.setattr(openai_module, "AsyncOpenAI", FakeAsyncOpenAI)
    return DeepSeekAdapter()


def test_adapter_uses_deepseek_credentials_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)

    assert adapter.provider is LLMProvider.DEEPSEEK
    assert adapter._client.kwargs["api_key"] == "deepseek-key"
    assert adapter._client.kwargs["base_url"] == "https://api.deepseek.com"
    assert adapter._default_model == "deepseek-chat"


@pytest.mark.asyncio
async def test_complete_labels_the_deepseek_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)

    result = await adapter.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="hi")], model="")
    )

    assert result.content == "deepseek answer"
    assert result.provider is LLMProvider.DEEPSEEK
    assert result.model == "deepseek-chat"
    assert result.total_tokens == 11
    call = adapter._client.chat.completions.calls[0]
    assert call["model"] == "deepseek-chat"


def test_factory_registers_deepseek_when_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        openai=OpenAISettings(_env_file=None, api_key=""),
        anthropic=AnthropicSettings(_env_file=None, api_key=""),
        gemini=GeminiSettings(_env_file=None, api_key=""),
        openrouter=OpenRouterSettings(_env_file=None, api_key=""),
        ollama=OllamaSettings(_env_file=None, enabled=False),
        deepseek=DeepSeekSettings(_env_file=None, api_key="deepseek-key"),
    )
    fake_adapter = SimpleNamespace(provider=LLMProvider.DEEPSEEK)
    monkeypatch.setattr(deepseek_module, "DeepSeekAdapter", lambda: fake_adapter)

    gateway = factory_module.create_llm_gateway(settings)

    assert gateway is not None
    assert gateway.configured_providers == ["deepseek"]
