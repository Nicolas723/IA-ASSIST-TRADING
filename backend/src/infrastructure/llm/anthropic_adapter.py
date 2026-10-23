"""
Anthropic Claude LLM Adapter.
"""

from __future__ import annotations

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for Anthropic Claude API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic.api_key)
        self._default_model = settings.anthropic.default_model
        self._max_tokens = settings.anthropic.max_tokens

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.ANTHROPIC

    @retry(
        retry=retry_if_exception_type(anthropic.RateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages
                if msg.role != "system"  # Anthropic uses separate system param
            ]

            response = await self._client.messages.create(
                model=request.model or self._default_model,
                max_tokens=min(request.max_tokens, self._max_tokens),
                temperature=request.temperature,
                system=request.system_prompt or "",
                messages=messages,
            )

            content = ""
            if response.content and response.content[0].type == "text":
                content = response.content[0].text

            return LLMResponse(
                content=content,
                model=response.model,
                provider=LLMProvider.ANTHROPIC,
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                raw_response=response.model_dump(),
                finish_reason=response.stop_reason or "stop",
            )

        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(str(exc), provider="anthropic") from exc
        except TimeoutError as exc:
            raise LLMTimeoutError("Anthropic request timed out", provider="anthropic") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError(f"Anthropic auth failed: {exc}", provider="anthropic") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}", provider="anthropic") from exc

    async def health_check(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False
