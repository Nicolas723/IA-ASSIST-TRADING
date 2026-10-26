"""Unit tests for the Anthropic Claude adapter error mapping."""

from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from src.infrastructure.llm import anthropic_adapter as anthropic_module
from src.infrastructure.llm.anthropic_adapter import AnthropicAdapter
from src.infrastructure.llm.gateway import LLMMessage, LLMRequest
from src.shared.config.settings import LLMProvider
from src.shared.exceptions.domain import (
    LLMError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
)

# anthropic 2.x moved its HTTP layer to httpx2; older releases use httpx.
_httpx = importlib.import_module("httpx2" if importlib.util.find_spec("httpx2") else "httpx")


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        anthropic=SimpleNamespace(
            api_key="claude-key",
            default_model="claude-opus-4-5",
            max_tokens=8192,
        )
    )


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeAsyncAnthropic:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.messages = FakeMessages()

    async def close(self) -> None:
        return None


def _build_adapter(monkeypatch: pytest.MonkeyPatch) -> AnthropicAdapter:
    config = _config()
    monkeypatch.setattr(anthropic_module, "get_settings", lambda: config)
    monkeypatch.setattr(anthropic_module.anthropic, "AsyncAnthropic", FakeAsyncAnthropic)
    return AnthropicAdapter()


def _status_error(
    error_type: type[anthropic.APIStatusError],
    status_code: int,
    message: str,
    headers: dict[str, str] | None = None,
) -> anthropic.APIStatusError:
    request = _httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = _httpx.Response(
        status_code,
        request=request,
        headers=headers or {},
        json={"error": {"message": message}},
    )
    return error_type(message, response=response, body=None)


def _request() -> LLMRequest:
    return LLMRequest(
        messages=[
            LLMMessage(role="system", content="ignored"),
            LLMMessage(role="user", content="hi"),
        ],
        model="",
        max_tokens=4096,
        system_prompt="eres un analista",
    )


def _success_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            anthropic.types.TextBlock(type="text", text="hola ", citations=None),
            anthropic.types.ThinkingBlock(type="thinking", thinking="ignored", signature="sig"),
            anthropic.types.TextBlock(type="text", text="mundo", citations=None),
        ],
        model="claude-opus-4-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        stop_reason="end_turn",
        model_dump=lambda: {"id": "msg_1"},
    )


def test_adapter_uses_configured_credentials_without_internal_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _build_adapter(monkeypatch)

    assert adapter.provider is LLMProvider.ANTHROPIC
    assert adapter._client.kwargs["api_key"] == "claude-key"
    assert adapter._client.kwargs["timeout"] == 60.0
    assert adapter._client.kwargs["max_retries"] == 0
    assert adapter._default_model == "claude-opus-4-5"


@pytest.mark.asyncio
async def test_complete_returns_text_blocks_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.messages = FakeMessages(response=_success_response())

    result = await adapter.complete(_request())

    assert result.content == "hola mundo"
    assert result.provider is LLMProvider.ANTHROPIC
    assert result.model == "claude-opus-4-5"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.total_tokens == 15
    assert result.finish_reason == "end_turn"
    call = adapter._client.messages.calls[0]
    assert call["model"] == "claude-opus-4-5"
    assert call["max_tokens"] == 4096
    assert call["system"] == "eres un analista"
    assert call["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_rate_limit_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.messages = FakeMessages(
        error=_status_error(
            anthropic.RateLimitError,
            429,
            "slow down",
            headers={"retry-after": "12"},
        )
    )

    with pytest.raises(LLMRateLimitError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.provider == "anthropic"
    assert excinfo.value.retry_after_seconds == 12


@pytest.mark.asyncio
async def test_rate_limit_without_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.messages = FakeMessages(
        error=_status_error(anthropic.RateLimitError, 429, "slow down")
    )

    with pytest.raises(LLMRateLimitError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.retry_after_seconds is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
    ],
)
async def test_rejected_credentials_park_the_provider(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[anthropic.APIStatusError],
    status_code: int,
) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.messages = FakeMessages(error=_status_error(error_type, status_code, "nope"))

    with pytest.raises(LLMQuotaError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.provider == "anthropic"
    assert excinfo.value.cooldown_seconds == 3600


@pytest.mark.asyncio
async def test_insufficient_balance_uses_a_shorter_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.messages = FakeMessages(
        error=_status_error(anthropic.APIStatusError, 402, "Insufficient Balance")
    )

    with pytest.raises(LLMQuotaError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.cooldown_seconds == 1800


@pytest.mark.asyncio
async def test_server_error_is_a_generic_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter._client.messages = FakeMessages(
        error=_status_error(anthropic.APIStatusError, 500, "boom")
    )

    with pytest.raises(LLMError) as excinfo:
        await adapter.complete(_request())

    assert not isinstance(excinfo.value, LLMQuotaError)
    assert excinfo.value.provider == "anthropic"


@pytest.mark.asyncio
async def test_timeout_maps_to_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _build_adapter(monkeypatch)
    request = _httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    adapter._client.messages = FakeMessages(error=anthropic.APITimeoutError(request))

    with pytest.raises(LLMTimeoutError) as excinfo:
        await adapter.complete(_request())

    assert excinfo.value.provider == "anthropic"
