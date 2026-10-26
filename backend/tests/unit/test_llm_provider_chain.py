"""The configured provider chain must follow Claude, ChatGPT, Google, DeepSeek."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from src.infrastructure.llm import (
    anthropic_adapter,
    deepseek_adapter,
    factory,
    gateway,
    gemini_adapter,
    openai_adapter,
)
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

if TYPE_CHECKING:
    import pytest


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    anthropic_key: str = "",
    openai_key: str = "",
    google_key: str = "",
    deepseek_key: str = "",
    primary: LLMProvider | None = None,
    fallbacks: list[LLMProvider] | None = None,
    fallback_enabled: bool = True,
) -> Settings:
    # Keep the developer's real .env out of the assertions.
    for variable in ("LLM_PRIMARY_PROVIDER", "LLM_FALLBACK_PROVIDERS", "LLM_FALLBACK_ENABLED"):
        monkeypatch.delenv(variable, raising=False)
    overrides: dict[str, object] = {}
    if primary is not None:
        overrides["llm_primary_provider"] = primary
    if fallbacks is not None:
        overrides["llm_fallback_providers"] = fallbacks
    if not fallback_enabled:
        overrides["llm_fallback_enabled"] = False
    return Settings(
        _env_file=None,
        openai=OpenAISettings(_env_file=None, api_key=openai_key),
        anthropic=AnthropicSettings(_env_file=None, api_key=anthropic_key),
        gemini=GeminiSettings(_env_file=None, api_key=google_key),
        deepseek=DeepSeekSettings(_env_file=None, api_key=deepseek_key),
        openrouter=OpenRouterSettings(_env_file=None, api_key=""),
        ollama=OllamaSettings(_env_file=None, enabled=False),
        **overrides,
    )


def _patch_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real SDK clients with stubs that only expose their provider."""
    for module, class_name, provider in (
        (anthropic_adapter, "AnthropicAdapter", LLMProvider.ANTHROPIC),
        (openai_adapter, "OpenAIAdapter", LLMProvider.OPENAI),
        (gemini_adapter, "GeminiAdapter", LLMProvider.GOOGLE),
        (deepseek_adapter, "DeepSeekAdapter", LLMProvider.DEEPSEEK),
    ):
        monkeypatch.setattr(
            module,
            class_name,
            lambda provider=provider: SimpleNamespace(provider=provider),
        )


def _gateway(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> gateway.LLMGateway | None:
    _patch_adapters(monkeypatch)
    monkeypatch.setattr(gateway, "get_settings", lambda: settings)
    return factory.create_llm_gateway(settings)


def test_defaults_request_claude_first(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)

    assert settings.llm_primary_provider is LLMProvider.ANTHROPIC
    assert settings.llm_fallback_providers == [
        LLMProvider.OPENAI,
        LLMProvider.GOOGLE,
        LLMProvider.DEEPSEEK,
    ]


def test_full_chain_keeps_the_requested_order(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        anthropic_key="claude-key",
        openai_key="openai-key",
        google_key="google-key",
        deepseek_key="deepseek-key",
    )

    result = _gateway(monkeypatch, settings)

    assert result is not None
    assert result.configured_providers == ["anthropic", "deepseek", "google", "openai"]
    assert result.primary_provider == "anthropic"
    assert result.fallback_providers == ["openai", "google", "deepseek"]


def test_missing_primary_key_promotes_the_first_configured_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Claude has no key yet: the chain must continue with ChatGPT.
    settings = _settings(
        monkeypatch,
        openai_key="openai-key",
        google_key="google-key",
        deepseek_key="deepseek-key",
    )

    result = _gateway(monkeypatch, settings)

    assert result is not None
    assert result.primary_provider == "openai"
    assert result.fallback_providers == ["google", "deepseek"]


def test_providers_without_keys_are_skipped_in_the_middle_of_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch,
        anthropic_key="claude-key",
        deepseek_key="deepseek-key",
    )

    result = _gateway(monkeypatch, settings)

    assert result is not None
    assert result.primary_provider == "anthropic"
    assert result.fallback_providers == ["deepseek"]


def test_disabled_fallbacks_report_no_secondary_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch,
        anthropic_key="claude-key",
        openai_key="openai-key",
        fallback_enabled=False,
    )

    result = _gateway(monkeypatch, settings)

    assert result is not None
    assert result.primary_provider == "anthropic"
    assert result.fallback_providers == []


def test_no_credentials_means_deterministic_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    _patch_adapters(monkeypatch)

    assert factory.create_llm_gateway(settings) is None
