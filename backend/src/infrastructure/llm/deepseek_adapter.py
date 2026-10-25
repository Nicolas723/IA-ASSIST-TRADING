"""
DeepSeek LLM Adapter.

DeepSeek exposes an OpenAI-compatible chat completions API, so this adapter
reuses the OpenAI transport and only changes credentials, base URL, default
model and provider labels.
"""

from __future__ import annotations

from src.infrastructure.llm.openai_adapter import OpenAIAdapter
from src.shared.config.settings import LLMProvider, get_settings


class DeepSeekAdapter(OpenAIAdapter):
    """Adapter for the DeepSeek API (``deepseek-chat`` / ``deepseek-reasoner``)."""

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            api_key=settings.deepseek.api_key,
            base_url=settings.deepseek.base_url,
            default_model=settings.deepseek.default_model,
            provider=LLMProvider.DEEPSEEK,
            provider_label="deepseek",
        )
