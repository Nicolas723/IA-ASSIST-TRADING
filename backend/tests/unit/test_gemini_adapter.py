"""Unit tests for the Google Gemini adapter (google-genai SDK)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.llm import gemini_adapter as gemini_module
from src.infrastructure.llm.gateway import LLMMessage, LLMRequest
from src.shared.config.settings import LLMProvider
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError


class FakeModels:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.generate_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.generate_calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return self.response

    async def get(self, *, model: str) -> Any:
        self.get_calls.append(model)
        if self.error is not None:
            raise self.error
        return object()


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = SimpleNamespace(models=models)


def make_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: Any = None,
    error: Exception | None = None,
) -> tuple[gemini_module.GeminiAdapter, FakeModels]:
    settings = SimpleNamespace(
        gemini=SimpleNamespace(api_key="test-key", default_model="gemini-test-model")
    )
    monkeypatch.setattr(gemini_module, "get_settings", lambda: settings)
    models = FakeModels(response=response, error=error)
    adapter = gemini_module.GeminiAdapter(client=FakeClient(models))  # type: ignore[arg-type]
    return adapter, models


def make_response(text: str = "OK") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=3,
            total_token_count=14,
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
    )


def make_request() -> LLMRequest:
    return LLMRequest(
        messages=[
            LLMMessage(role="system", content="ignored-by-adapter"),
            LLMMessage(role="user", content="Analyze BTC"),
            LLMMessage(role="assistant", content="Previous answer"),
            LLMMessage(role="user", content="Continue"),
        ],
        model="",
        max_tokens=256,
        temperature=0.2,
        system_prompt="You are a trading analyst.",
    )


@pytest.mark.asyncio
async def test_complete_maps_contents_config_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, models = make_adapter(monkeypatch, response=make_response())

    result = await adapter.complete(make_request())

    assert result.content == "OK"
    assert result.model == "gemini-test-model"
    assert result.provider is LLMProvider.GOOGLE
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (11, 3, 14)
    assert result.finish_reason == "STOP"

    call = models.generate_calls[0]
    assert call["model"] == "gemini-test-model"
    assert call["config"].system_instruction == "You are a trading analyst."
    assert call["config"].max_output_tokens == 256
    assert [content.role for content in call["contents"]] == ["user", "user", "model", "user"]
    assert call["contents"][0].parts[0].text == "ignored-by-adapter"


@pytest.mark.asyncio
async def test_complete_maps_rate_limit_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = make_adapter(monkeypatch, error=RuntimeError("429 RESOURCE_EXHAUSTED quota"))

    with pytest.raises(LLMRateLimitError):
        await adapter.complete.__wrapped__(adapter, make_request())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_complete_maps_timeout_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = make_adapter(monkeypatch, error=TimeoutError("deadline"))

    with pytest.raises(LLMTimeoutError):
        await adapter.complete.__wrapped__(adapter, make_request())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_complete_maps_generic_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = make_adapter(monkeypatch, error=RuntimeError("boom"))

    with pytest.raises(LLMError):
        await adapter.complete.__wrapped__(adapter, make_request())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_health_check_uses_model_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, models = make_adapter(monkeypatch)

    assert await adapter.health_check() is True
    assert models.get_calls == ["gemini-test-model"]


@pytest.mark.asyncio
async def test_health_check_returns_false_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = make_adapter(monkeypatch, error=RuntimeError("no access"))

    assert await adapter.health_check() is False
