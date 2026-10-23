"""
Google Gemini LLM Adapter.
"""

from __future__ import annotations

import asyncio

import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMRateLimitError, LLMTimeoutError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class GeminiAdapter(BaseLLMAdapter):
    """Adapter for Google Gemini API."""

    def __init__(self) -> None:
        settings = get_settings()
        genai.configure(api_key=settings.gemini.api_key)
        self._default_model = settings.gemini.default_model

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.GOOGLE

    @retry(
        retry=retry_if_exception_type(Exception),  # Specific exceptions can be added here
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            model_name = request.model or self._default_model

            # Setup GenerationConfig
            generation_config = genai.types.GenerationConfig(
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
            )

            # Map messages format
            contents = []
            for msg in request.messages:
                # Map roles: 'assistant' -> 'model'
                role = "model" if msg.role == "assistant" else "user"
                contents.append({"role": role, "parts": [msg.content]})

            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=request.system_prompt,
                generation_config=generation_config,
                # Trading analysis might trigger safety filters, setting threshold to BLOCK_NONE or BLOCK_ONLY_HIGH
                safety_settings={
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                },
            )

            # Gemini Python SDK doesn't natively support full async yet, we use asyncio.to_thread
            response = await asyncio.to_thread(model.generate_content, contents)

            return LLMResponse(
                content=response.text,
                model=model_name,
                provider=LLMProvider.GOOGLE,
                prompt_tokens=response.usage_metadata.prompt_token_count
                if response.usage_metadata
                else 0,
                completion_tokens=response.usage_metadata.candidates_token_count
                if response.usage_metadata
                else 0,
                total_tokens=response.usage_metadata.total_token_count
                if response.usage_metadata
                else 0,
                raw_response=response.to_dict() if hasattr(response, "to_dict") else {},
                finish_reason=str(response.candidates[0].finish_reason)
                if response.candidates
                else "stop",
            )

        except TimeoutError as exc:
            raise LLMTimeoutError("Gemini request timed out", provider="google") from exc
        except Exception as exc:
            # Need to map specific Google API errors to LLMRateLimitError if 429
            err_str = str(exc).lower()
            if "429" in err_str or "quota" in err_str:
                raise LLMRateLimitError(str(exc), provider="google") from exc
            raise LLMError(f"Gemini API error: {exc}", provider="google") from exc

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(genai.list_models)
            return True
        except Exception:
            return False
