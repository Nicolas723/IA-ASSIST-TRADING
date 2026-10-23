"""
Ollama Local LLM Adapter — for privacy, offline use, and zero-cost inference.
"""

from __future__ import annotations

import aiohttp

from src.infrastructure.llm.gateway import BaseLLMAdapter, LLMRequest, LLMResponse
from src.shared.config.settings import LLMProvider, get_settings
from src.shared.exceptions.domain import LLMError, LLMTimeoutError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class OllamaAdapter(BaseLLMAdapter):
    """
    Adapter for local Ollama inference server.

    Ollama must be running locally at the configured base_url.
    No API key required — runs on local hardware.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.ollama.base_url.rstrip("/")
        self._default_model = settings.ollama.default_model

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.OLLAMA

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.extend({"role": msg.role, "content": msg.content} for msg in request.messages)

            payload = {
                "model": request.model or self._default_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": request.temperature,
                    "num_predict": request.max_tokens,
                },
            }

            timeout = aiohttp.ClientTimeout(total=120)  # Local models can be slow

            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                ) as resp,
            ):
                if resp.status != 200:
                    body = await resp.text()
                    raise LLMError(
                        f"Ollama returned HTTP {resp.status}: {body}",
                        provider="ollama",
                    )
                data = await resp.json()

            content = data.get("message", {}).get("content", "")
            prompt_eval_count = data.get("prompt_eval_count", 0)
            eval_count = data.get("eval_count", 0)

            return LLMResponse(
                content=content,
                model=data.get("model", request.model or self._default_model),
                provider=LLMProvider.OLLAMA,
                prompt_tokens=prompt_eval_count,
                completion_tokens=eval_count,
                total_tokens=prompt_eval_count + eval_count,
                raw_response=data,
                finish_reason=data.get("done_reason", "stop"),
            )

        except aiohttp.ServerTimeoutError as exc:
            raise LLMTimeoutError("Ollama request timed out", provider="ollama") from exc
        except aiohttp.ClientConnectorError as exc:
            raise LLMError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is Ollama running? Run: ollama serve",
                provider="ollama",
            ) from exc
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Ollama error: {exc}", provider="ollama") from exc

    async def health_check(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(f"{self._base_url}/api/tags") as resp,
            ):
                return resp.status == 200
        except Exception:
            return False
