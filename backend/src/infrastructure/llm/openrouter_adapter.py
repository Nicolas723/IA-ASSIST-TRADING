"""
OpenRouter LLM Adapter.
Acts as a gateway to multiple models via openrouter.ai
"""

from __future__ import annotations

from typing import Any, cast

import openai
from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class OpenRouterAdapter(BaseLLMAdapter):
    """Adapter for OpenRouter API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.openrouter.api_key,
            base_url=settings.openrouter.base_url,
            timeout=60.0,
            max_retries=0,
        )

        self._default_model = settings.openrouter.default_model

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.OPENROUTER

    @retry(
        retry=retry_if_exception_type((openai.RateLimitError, openai.APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            for msg in request.messages:
                messages.append({"role": msg.role, "content": msg.content})

            response = await self._client.chat.completions.create(
                model=request.model or self._default_model,
                messages=cast("Any", messages),
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                # Extra headers for OpenRouter
                extra_headers={
                    "HTTP-Referer": "http://localhost:8000",
                    "X-Title": "AI Trading Assistant",
                },
            )

            choice = response.choices[0]
            usage = response.usage

            return LLMResponse(
                content=choice.message.content or "",
                model=response.model,
                provider=LLMProvider.OPENROUTER,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response.model_dump(),
                finish_reason=choice.finish_reason or "stop",
            )

        except openai.RateLimitError as exc:
            raise LLMRateLimitError(str(exc), provider="openrouter") from exc
        except TimeoutError as exc:
            raise LLMTimeoutError("OpenRouter request timed out", provider="openrouter") from exc
        except openai.AuthenticationError as exc:
            raise LLMError(f"OpenRouter auth failed: {exc}", provider="openrouter") from exc
        except openai.APIError as exc:
            raise LLMError(f"OpenRouter API error: {exc}", provider="openrouter") from exc

    async def health_check(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False
