"""
Google Gemini LLM Adapter.

Uses the current ``google-genai`` SDK (the legacy ``google-generativeai``
package reached end of support). The async client keeps the request off the
event loop without a thread hop.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=category,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    )
    for category in (
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    )
]


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


def _is_rate_limit(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "quota" in text or "resource_exhausted" in text


def _response_text(response: Any) -> str:
    try:
        return response.text or ""
    except Exception:
        # A blocked or empty candidate has no text; the caller decides how to
        # treat an empty completion.
        return ""


def _finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "stop"
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return "stop"
    return getattr(reason, "name", None) or str(reason)


class GeminiAdapter(BaseLLMAdapter):
    """Adapter for Google Gemini API."""

    def __init__(self, client: genai.Client | None = None) -> None:
        settings = get_settings()
        self._client = client or genai.Client(api_key=settings.gemini.api_key)
        self._default_model = settings.gemini.default_model

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.GOOGLE

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            model_name = request.model or self._default_model

            contents = [
                types.Content(
                    role="model" if msg.role == "assistant" else "user",
                    parts=[types.Part(text=msg.content)],
                )
                for msg in request.messages
            ]
            config = types.GenerateContentConfig(
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
                system_instruction=request.system_prompt,
                safety_settings=_SAFETY_SETTINGS,
            )

            response = await self._client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )

            usage = response.usage_metadata
            raw = response.model_dump() if hasattr(response, "model_dump") else {}
            return LLMResponse(
                content=_response_text(response),
                model=model_name,
                provider=LLMProvider.GOOGLE,
                prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                total_tokens=getattr(usage, "total_token_count", 0) or 0,
                raw_response=raw,
                finish_reason=_finish_reason(response),
            )

        except Exception as exc:
            if _is_timeout(exc):
                raise LLMTimeoutError("Gemini request timed out", provider="google") from exc
            if _is_rate_limit(exc):
                raise LLMRateLimitError(str(exc), provider="google") from exc
            raise LLMError(f"Gemini API error: {exc}", provider="google") from exc

    async def health_check(self) -> bool:
        try:
            await self._client.aio.models.get(model=self._default_model)
            return True
        except Exception:
            return False
