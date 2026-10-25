"""
OpenAI-compatible LLM Adapter.

Implements BaseLLMAdapter for OpenAI's API (GPT-4o, GPT-5.5, o3, etc.) and for
any provider that exposes the same chat-completions contract (for example
DeepSeek). Handles retries, rate limiting, and token tracking.
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
from src.shared.exceptions.domain import (
    LLMError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class OpenAIAdapter(BaseLLMAdapter):
    """
    Adapter for OpenAI and OpenAI-compatible APIs.

    Supports: GPT-4o, GPT-4o-mini, GPT-5.5, o1, o3, o3-mini, DeepSeek chat.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        provider: LLMProvider = LLMProvider.OPENAI,
        provider_label: str = "openai",
    ) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=api_key if api_key is not None else settings.openai.api_key,
            base_url=base_url,
            timeout=60.0,
            max_retries=0,  # We handle retries with tenacity
        )
        self._default_model = default_model or settings.openai.default_model
        self._provider = provider
        self._provider_label = provider_label

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @retry(
        # A 429 may be a per-minute limit or a daily quota. Retrying it can
        # consume the whole daily allowance, so only connection/timeouts retry.
        retry=retry_if_exception_type(openai.APIConnectionError),
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
                provider=self._provider,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response.model_dump(),
                finish_reason=choice.finish_reason or "stop",
            )

        except openai.RateLimitError as exc:
            raise LLMRateLimitError(str(exc), provider=self._provider_label) from exc
        except TimeoutError as exc:
            raise LLMTimeoutError(
                f"{self._provider_label} request timed out", provider=self._provider_label
            ) from exc
        except openai.AuthenticationError as exc:
            raise LLMQuotaError(
                f"{self._provider_label} rejected the API key: {exc}",
                provider=self._provider_label,
                cooldown_seconds=3600,
            ) from exc
        except openai.PermissionDeniedError as exc:
            raise LLMQuotaError(
                f"{self._provider_label} denied access to this model: {exc}",
                provider=self._provider_label,
                cooldown_seconds=3600,
            ) from exc
        except openai.APIStatusError as exc:
            # 402 = the account is out of credit. It will not recover within a
            # cycle, so park the provider instead of retrying every analysis.
            if exc.status_code == 402:
                raise LLMQuotaError(
                    f"{self._provider_label} has insufficient balance: {exc}",
                    provider=self._provider_label,
                    cooldown_seconds=1800,
                ) from exc
            raise LLMError(
                f"{self._provider_label} API error: {exc}", provider=self._provider_label
            ) from exc
        except openai.OpenAIError as exc:
            raise LLMError(
                f"{self._provider_label} API error: {exc}", provider=self._provider_label
            ) from exc

    async def health_check(self) -> bool:
        try:
            # Lightweight health check — list models
            await self._client.models.list()
            return True
        except Exception:
            return False
