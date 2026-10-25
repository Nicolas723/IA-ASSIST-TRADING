"""Unit tests for the DeepSeek adapter and its factory wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
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
from src.shared.exceptions.domain import LLMQuotaError


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


class RaisingCompletions:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def create(self, **kwargs: Any) -> Any:
        raise self.error


def _status_error(status_code: int, message: str) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": {"message": message}})
    if status_code == 401:
        return openai.AuthenticationError(message, response=response, body=None)
    if status_code == 403:
        return openai.PermissionDeniedError(message, response=response, body=None)
    return openai.APIStatusError(message, response=response, body=None)


def _request() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")], model="")


@pytest.mark.asyncio
async def test_insufficient_balance_maps_to_a_long_quota_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.chat.completions = RaisingCompletions(  # type: ignore[assignment]
        _status_error(402, "Insufficient Balance")
    )

    with pytest.raises(LLMQuotaError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.provider == "deepseek"
    assert excinfo.value.cooldown_seconds == 1800


@pytest.mark.asyncio
async def test_rejected_credentials_map_to_a_quota_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.chat.completions = RaisingCompletions(  # type: ignore[assignment]
        _status_error(401, "Invalid API key")
    )

    with pytest.raises(LLMQuotaError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.cooldown_seconds == 3600
