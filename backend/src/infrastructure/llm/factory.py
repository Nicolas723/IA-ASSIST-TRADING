"""Build the LLM gateway from providers that have been explicitly configured."""

from __future__ import annotations

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMGateway
from src.shared.config.settings import LLMProvider, Settings, get_settings
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


def create_llm_gateway(settings: Settings | None = None) -> LLMGateway | None:
    """Return a gateway with configured credentials, or None for rules-only mode."""
    config = settings or get_settings()
    adapters: dict[LLMProvider, BaseLLMAdapter] = {}

    try:
        if config.openai.api_key.strip():
            from src.infrastructure.llm.openai_adapter import OpenAIAdapter

            adapters[LLMProvider.OPENAI] = OpenAIAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="openai", error=str(exc))

    try:
        if config.gemini.api_key.strip():
            from src.infrastructure.llm.gemini_adapter import GeminiAdapter

            adapters[LLMProvider.GOOGLE] = GeminiAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="google", error=str(exc))

    try:
        if config.anthropic.api_key.strip():
            from src.infrastructure.llm.anthropic_adapter import AnthropicAdapter

            adapters[LLMProvider.ANTHROPIC] = AnthropicAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="anthropic", error=str(exc))

    try:
        if config.openrouter.api_key.strip():
            from src.infrastructure.llm.openrouter_adapter import OpenRouterAdapter

            adapters[LLMProvider.OPENROUTER] = OpenRouterAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="openrouter", error=str(exc))

    try:
        if config.deepseek.api_key.strip():
            from src.infrastructure.llm.deepseek_adapter import DeepSeekAdapter

            adapters[LLMProvider.DEEPSEEK] = DeepSeekAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="deepseek", error=str(exc))

    try:
        if config.groq.api_key.strip():
            from src.infrastructure.llm.groq_adapter import GroqAdapter

            adapters[LLMProvider.GROQ] = GroqAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="groq", error=str(exc))

    try:
        if config.ollama.enabled:
            from src.infrastructure.llm.ollama_adapter import OllamaAdapter

            adapters[LLMProvider.OLLAMA] = OllamaAdapter()
    except ImportError as exc:
        logger.warning("llm_provider_sdk_missing", provider="ollama", error=str(exc))

    if not adapters:
        logger.warning("llm_no_providers_configured_using_deterministic_fallback")
        return None

    logger.info(
        "llm_gateway_initialized",
        primary=config.llm_primary_provider.value,
        configured_providers=sorted(provider.value for provider in adapters),
        fallbacks_enabled=config.llm_fallback_enabled,
    )
    return LLMGateway(adapters)
