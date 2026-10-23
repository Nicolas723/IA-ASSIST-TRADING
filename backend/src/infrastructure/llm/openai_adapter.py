"""
OpenAI LLM Adapter.

Implements BaseLLMAdapter for OpenAI's API (GPT-4o, GPT-5.5, o3, etc.)
Handles retries, rate limiting, and token tracking.
"""

from __future__ import annotations

from typing import Any

import openai
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class OpenAIAdapter(BaseLLMAdapter):
    """
    Adapter for OpenAI API.

    Supports: GPT-4o, GPT-4o-mini, GPT-5.5, o1, o3, o3-mini
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.openai.api_key,
            timeout=60.0,
            max_retries=0,  # We handle retries with tenacity
        )
        self._default_model = settings.openai.default_model

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.OPENAI

    @retry(
        retry=retry_if_exception_type((openai.RateLimitError, openai.APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            messages: list[dict[str, str]] = []

            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})

            messages.extend({"role": msg.role, "content": msg.content} for msg in request.messages)

            kwargs: dict[str, Any] = {
                "model": request.model or self._default_model,
                "messages": messages,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            }

            if request.response_format == "json":
                kwargs["response_format"] = {"type": "json_object"}

            response = await self._client.chat.completions.create(**kwargs)

            choice = response.choices[0]
            usage = response.usage

            return LLMResponse(
                content=choice.message.content or "",
                model=response.model,
                provider=LLMProvider.OPENAI,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response.model_dump(),
                finish_reason=choice.finish_reason or "stop",
            )

        except openai.RateLimitError as exc:
            raise LLMRateLimitError(str(exc), provider="openai") from exc
        except TimeoutError as exc:
            raise LLMTimeoutError("OpenAI request timed out", provider="openai") from exc
        except openai.AuthenticationError as exc:
            raise LLMError(f"OpenAI authentication failed: {exc}", provider="openai") from exc
        except openai.OpenAIError as exc:
            raise LLMError(f"OpenAI API error: {exc}", provider="openai") from exc

    async def health_check(self) -> bool:
        try:
            # Lightweight health check — list models
            await self._client.models.list()
            return True
        except Exception:
            return False
