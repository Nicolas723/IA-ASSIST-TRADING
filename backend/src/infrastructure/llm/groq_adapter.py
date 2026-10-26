"""
Groq LLM Adapter.

Groq exposes an OpenAI-compatible chat completions API with a permanent free
tier, so this adapter reuses the OpenAI transport and only changes credentials,
base URL, default model and provider labels.
"""

from __future__ import annotations

from src.infrastructure.llm.openai_adapter import OpenAIAdapter
from src.shared.config.settings import LLMProvider, get_settings


class GroqAdapter(OpenAIAdapter):
    """Adapter for Groq (``openai/gpt-oss-120b``, ``qwen/qwen3.8-27b``)."""

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            api_key=settings.groq.api_key,
            base_url=settings.groq.base_url,
            default_model=settings.groq.default_model,
            provider=LLMProvider.GROQ,
            provider_label="groq",
        )
