"""
Anthropic Claude LLM Adapter.

The gateway owns retries and fallbacks, so this adapter makes exactly one call
and maps every SDK failure to a domain error. That keeps rate limits, billing
problems and bad credentials visible to the routing layer instead of being
swallowed by a local retry loop.
"""

from __future__ import annotations

from typing import cast

import anthropic

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import (
    LLMError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
)


def _retry_after_seconds(exc: anthropic.APIStatusError) -> int | None:
    """Read the provider's Retry-After header when it is a positive integer."""
    try:
        value = exc.response.headers.get("retry-after")
        if value is None:
            return None
        seconds: int = int(float(value))
    except (AttributeError, TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for the Anthropic Claude Messages API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic.api_key,
            timeout=60.0,
            max_retries=0,  # The gateway decides when to try the next provider.
        )
        self._default_model = settings.anthropic.default_model
        self._max_tokens = settings.anthropic.max_tokens

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.ANTHROPIC

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            messages = cast(
                "list[anthropic.types.MessageParam]",
                [
                    {"role": msg.role, "content": msg.content}
                    for msg in request.messages
                    if msg.role != "system"  # Anthropic uses a separate system param
                ],
            )

            response = await self._client.messages.create(
                model=request.model or self._default_model,
                max_tokens=min(request.max_tokens, self._max_tokens),
                # Current Claude models reject the sampling parameters, so the
                # adapter relies on the provider default instead of sending
                # request.temperature.
                system=request.system_prompt or "",
                messages=messages,
            )

            content = "".join(
                block.text
                for block in response.content
                if isinstance(block, anthropic.types.TextBlock)
            )

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
            raise LLMRateLimitError(
                str(exc),
                provider="anthropic",
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc
        except anthropic.AuthenticationError as exc:
            # A rejected key will not fix itself: park the provider so the
            # daemon does not spend an attempt on every cycle.
            raise LLMQuotaError(
                f"Anthropic rejected the API key: {exc}",
                provider="anthropic",
                cooldown_seconds=3600,
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMQuotaError(
                f"Anthropic denied access to this model: {exc}",
                provider="anthropic",
                cooldown_seconds=3600,
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402:
                raise LLMQuotaError(
                    f"Anthropic has insufficient balance: {exc}",
                    provider="anthropic",
                    cooldown_seconds=1800,
                ) from exc
            raise LLMError(f"Anthropic API error: {exc}", provider="anthropic") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("Anthropic request timed out", provider="anthropic") from exc
        except TimeoutError as exc:
            raise LLMTimeoutError("Anthropic request timed out", provider="anthropic") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic connection error: {exc}", provider="anthropic") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}", provider="anthropic") from exc

    async def health_check(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False
