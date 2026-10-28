"""UI-managed integration settings: LLM providers, Telegram and heartbeat.

The trading configuration already has a runtime override service. This service
covers the remaining credentials and endpoints that used to require editing
``backend/.env``:

* LLM provider API keys, models and routing.
* Telegram bot token / chat id and the alert switch.
* Outbound heartbeat URL and interval.

Secrets are written to an encrypted store (``SecureSettingsStore``) and applied
to the cached ``Settings`` instance, so changes take effect without editing
files or restarting the process. The gateway and alert singletons are rebuilt by
the API layer after a save.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.infrastructure.llm.factory import create_llm_gateway
from src.infrastructure.security.secure_settings_store import SecureSettingsStore
from src.shared.config.settings import (
    LLMChainEntry,
    LLMProvider,
    Settings,
    get_settings,
)
from src.shared.exceptions.domain import ValidationError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

# Settings attribute and UI capabilities for every supported provider.
PROVIDER_META: dict[LLMProvider, dict[str, Any]] = {
    LLMProvider.OPENAI: {
        "label": "OpenAI",
        "attr": "openai",
        "requires_key": True,
        "supports_base_url": False,
        "default_base_url": None,
    },
    LLMProvider.ANTHROPIC: {
        "label": "Anthropic (Claude)",
        "attr": "anthropic",
        "requires_key": True,
        "supports_base_url": False,
        "default_base_url": None,
    },
    LLMProvider.GOOGLE: {
        "label": "Google Gemini",
        "attr": "gemini",
        "requires_key": True,
        "supports_base_url": False,
        "default_base_url": None,
    },
    LLMProvider.OPENROUTER: {
        "label": "OpenRouter",
        "attr": "openrouter",
        "requires_key": True,
        "supports_base_url": True,
        "default_base_url": "https://openrouter.ai/api/v1",
    },
    LLMProvider.DEEPSEEK: {
        "label": "DeepSeek",
        "attr": "deepseek",
        "requires_key": True,
        "supports_base_url": True,
        "default_base_url": "https://api.deepseek.com",
    },
    LLMProvider.GROQ: {
        "label": "Groq",
        "attr": "groq",
        "requires_key": True,
        "supports_base_url": True,
        "default_base_url": "https://api.groq.com/openai/v1",
    },
    LLMProvider.OLLAMA: {
        "label": "Ollama (local)",
        "attr": "ollama",
        "requires_key": False,
        "supports_base_url": True,
        "default_base_url": "http://localhost:11434",
    },
}


_NON_CHAT_MODEL_TAGS = (
    "whisper",
    "tts",
    "orpheus",
    "playai",
    "guard",
    "safety",
    "lyria",
    "embed",
    "image",
)


def _mask(secret: str) -> str:
    value = (secret or "").strip()
    if not value:
        return ""
    if len(value) < 12:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


class LLMProviderView(BaseModel):
    provider: LLMProvider
    label: str
    configured: bool
    masked_key: str
    model: str
    base_url: str | None
    requires_key: bool
    supports_base_url: bool
    enabled: bool


class LLMSettingsView(BaseModel):
    providers: list[LLMProviderView]
    primary_provider: LLMProvider
    fallback_providers: list[LLMProvider]
    fallback_enabled: bool
    # Effective routing order. Derived from primary/fallbacks until the user
    # saves an explicit chain, so the UI always starts from what runs today.
    model_chain: list[LLMChainEntry] = Field(default_factory=list)
    configured_providers: list[str]
    scan_mode: str
    note: str = (
        "Las claves se guardan cifradas y nunca se devuelven en claro. "
        "Guardar una clave no activa ninguna operación: solo habilita al proveedor "
        "para el comité de análisis."
    )


class TelegramSettingsView(BaseModel):
    configured: bool
    masked_token: str
    chat_id: str
    enabled: bool


class HeartbeatSettingsView(BaseModel):
    configured: bool
    masked_url: str
    interval_seconds: int
    running: bool


class IntegrationSettingsView(BaseModel):
    llm: LLMSettingsView
    telegram: TelegramSettingsView
    heartbeat: HeartbeatSettingsView


class LLMProviderUpdate(BaseModel):
    provider: LLMProvider
    api_key: str | None = Field(
        default=None,
        description="New key. Omit or send null to keep the stored one.",
    )
    clear_key: bool = False
    model: str | None = None
    base_url: str | None = None
    enabled: bool | None = None


class LLMSettingsUpdate(BaseModel):
    providers: list[LLMProviderUpdate] = Field(default_factory=list)
    primary_provider: LLMProvider | None = None
    fallback_providers: list[LLMProvider] | None = None
    fallback_enabled: bool | None = None
    model_chain: list[LLMChainEntry] | None = Field(
        default=None,
        description="Ordered provider+model attempts. An empty list returns to primary/fallbacks.",
    )


class LLMModelOption(BaseModel):
    id: str
    name: str
    free: bool
    context_length: int | None = None


class TelegramSettingsUpdate(BaseModel):
    bot_token: str | None = None
    clear_token: bool = False
    chat_id: str | None = None
    enabled: bool | None = None


class HeartbeatSettingsUpdate(BaseModel):
    ping_url: str | None = None
    clear_url: bool = False
    interval_seconds: int | None = Field(default=None, ge=15, le=3600)


class IntegrationSettingsService:
    """Read, validate, persist and apply integration settings."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        store: SecureSettingsStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or SecureSettingsStore()

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> IntegrationSettingsView:
        return IntegrationSettingsView(
            llm=self._llm_view(),
            telegram=self._telegram_view(),
            heartbeat=self._heartbeat_view(),
        )

    def _llm_view(self) -> LLMSettingsView:
        providers: list[LLMProviderView] = []
        configured: list[str] = []
        for provider, meta in PROVIDER_META.items():
            block = getattr(self.settings, meta["attr"])
            key = str(getattr(block, "api_key", "") or "")
            enabled = bool(getattr(block, "enabled", True))
            is_configured = enabled and (bool(key.strip()) if meta["requires_key"] else True)
            if is_configured:
                configured.append(provider.value)
            providers.append(
                LLMProviderView(
                    provider=provider,
                    label=meta["label"],
                    configured=is_configured,
                    masked_key=_mask(key),
                    model=str(getattr(block, "default_model", "") or ""),
                    base_url=(
                        str(getattr(block, "base_url", "") or "")
                        if meta["supports_base_url"]
                        else None
                    ),
                    requires_key=meta["requires_key"],
                    supports_base_url=meta["supports_base_url"],
                    enabled=enabled,
                )
            )
        return LLMSettingsView(
            providers=providers,
            primary_provider=self.settings.llm_primary_provider,
            fallback_providers=list(self.settings.llm_fallback_providers),
            fallback_enabled=self.settings.llm_fallback_enabled,
            model_chain=self._effective_chain(),
            configured_providers=configured,
            scan_mode=self.settings.llm_scan_mode.value,
        )

    def _effective_chain(self) -> list[LLMChainEntry]:
        if self.settings.llm_model_chain:
            return list(self.settings.llm_model_chain)
        order = [self.settings.llm_primary_provider]
        if self.settings.llm_fallback_enabled:
            order += [p for p in self.settings.llm_fallback_providers if p not in order]
        return [LLMChainEntry(provider=provider) for provider in order]

    def _telegram_view(self) -> TelegramSettingsView:
        token = self.settings.telegram_bot_token or ""
        chat_id = self.settings.telegram_chat_id or ""
        return TelegramSettingsView(
            configured=bool(token.strip() and chat_id.strip()),
            masked_token=_mask(token),
            chat_id=chat_id,
            enabled=bool(self.settings.alerts_enabled),
        )

    def _heartbeat_view(self) -> HeartbeatSettingsView:
        from src.application.services.heartbeat_service import heartbeat_manager

        url = self.settings.heartbeat_ping_url or ""
        return HeartbeatSettingsView(
            configured=bool(url.strip()),
            masked_url=_mask(url),
            interval_seconds=int(self.settings.heartbeat_interval_seconds),
            running=heartbeat_manager.running,
        )

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def update_llm(self, payload: LLMSettingsUpdate) -> IntegrationSettingsView:
        stored = self.store.section("llm")
        providers: dict[str, Any] = dict(stored.get("providers") or {})

        # Seed from the effective settings so a partial UI save does not drop
        # values that came from .env before the first edit.
        stored.setdefault("primary_provider", self.settings.llm_primary_provider.value)
        stored.setdefault(
            "fallback_providers",
            [provider.value for provider in self.settings.llm_fallback_providers],
        )
        stored.setdefault("fallback_enabled", self.settings.llm_fallback_enabled)

        for update in payload.providers:
            meta = PROVIDER_META[update.provider]
            block = getattr(self.settings, meta["attr"])
            current = dict(providers.get(update.provider.value) or {})
            if meta["requires_key"]:
                current.setdefault("api_key", str(getattr(block, "api_key", "") or ""))
            else:
                # Keyless providers (Ollama) have no api_key field to assign.
                current.pop("api_key", None)
            current.setdefault("model", str(getattr(block, "default_model", "") or ""))
            if meta["supports_base_url"]:
                current.setdefault("base_url", str(getattr(block, "base_url", "") or ""))
            if hasattr(block, "enabled"):
                current.setdefault("enabled", bool(getattr(block, "enabled", True)))

            if not meta["requires_key"]:
                pass
            elif update.clear_key:
                current["api_key"] = ""
            elif update.api_key is not None and update.api_key.strip():
                current["api_key"] = update.api_key.strip()
            if update.model is not None and update.model.strip():
                current["model"] = update.model.strip()
            if meta["supports_base_url"] and update.base_url is not None:
                current["base_url"] = update.base_url.strip()
            if update.enabled is not None:
                current["enabled"] = bool(update.enabled)
            providers[update.provider.value] = current

        if payload.primary_provider is not None:
            stored["primary_provider"] = payload.primary_provider.value
        if payload.fallback_providers is not None:
            unique = list(dict.fromkeys(provider.value for provider in payload.fallback_providers))
            stored["fallback_providers"] = unique
        # The primary must not also appear as its own fallback, or a failing
        # provider would be retried twice in the same chain.
        stored["fallback_providers"] = [
            provider
            for provider in stored["fallback_providers"]
            if provider != stored["primary_provider"]
        ]
        if payload.fallback_enabled is not None:
            stored["fallback_enabled"] = bool(payload.fallback_enabled)
        if payload.model_chain is not None:
            entries: dict[str, LLMChainEntry] = {}
            for entry in payload.model_chain:
                cleaned = entry.model_copy(update={"model": entry.model.strip()})
                entries.setdefault(cleaned.key, cleaned)
            stored["model_chain"] = [entry.model_dump(mode="json") for entry in entries.values()]
        stored["providers"] = providers

        self.store.set_section("llm", stored)
        self._apply_llm(stored)
        logger.info(
            "llm_settings_updated",
            providers=sorted(providers),
            primary=str(stored.get("primary_provider") or ""),
        )
        return self.snapshot()

    def update_telegram(self, payload: TelegramSettingsUpdate) -> IntegrationSettingsView:
        stored = self.store.section("telegram")
        stored.setdefault("bot_token", self.settings.telegram_bot_token or "")
        stored.setdefault("chat_id", self.settings.telegram_chat_id or "")
        stored.setdefault("enabled", bool(self.settings.alerts_enabled))
        if payload.clear_token:
            stored["bot_token"] = ""
        elif payload.bot_token is not None and payload.bot_token.strip():
            stored["bot_token"] = payload.bot_token.strip()
        if payload.chat_id is not None:
            stored["chat_id"] = payload.chat_id.strip()
        if payload.enabled is not None:
            stored["enabled"] = bool(payload.enabled)
        self.store.set_section("telegram", stored)
        self._apply_telegram(stored)
        logger.info("telegram_settings_updated", configured=bool(stored.get("bot_token")))
        return self.snapshot()

    async def update_heartbeat(self, payload: HeartbeatSettingsUpdate) -> IntegrationSettingsView:
        from src.application.services.heartbeat_service import heartbeat_manager

        stored = self.store.section("heartbeat")
        stored.setdefault("ping_url", self.settings.heartbeat_ping_url or "")
        stored.setdefault("interval_seconds", int(self.settings.heartbeat_interval_seconds))
        if payload.clear_url:
            stored["ping_url"] = ""
        elif payload.ping_url is not None and payload.ping_url.strip():
            stored["ping_url"] = payload.ping_url.strip()
        if payload.interval_seconds is not None:
            stored["interval_seconds"] = int(payload.interval_seconds)
        self.store.set_section("heartbeat", stored)
        self._apply_heartbeat(stored)
        await heartbeat_manager.restart(
            url=str(stored.get("ping_url") or ""),
            interval_seconds=int(stored.get("interval_seconds") or 60),
        )
        logger.info("heartbeat_settings_updated", enabled=bool(stored.get("ping_url")))
        return self.snapshot()

    # ------------------------------------------------------------------
    # Apply persisted values
    # ------------------------------------------------------------------
    def apply_persisted(self) -> None:
        data = self.store.as_dict()
        if data.get("llm"):
            self._apply_llm(data["llm"])
        if data.get("telegram"):
            self._apply_telegram(data["telegram"])
        if data.get("heartbeat"):
            self._apply_heartbeat(data["heartbeat"])

    def _apply_llm(self, stored: dict[str, Any]) -> None:
        providers = stored.get("providers")
        if isinstance(providers, dict):
            for provider, meta in PROVIDER_META.items():
                values = providers.get(provider.value)
                if not isinstance(values, dict):
                    continue
                block = getattr(self.settings, meta["attr"])
                api_key = values.get("api_key")
                # Older saves stored an empty key for Ollama, which has no such
                # field; assigning it aborted the whole apply (and every later
                # provider and the routing order with it).
                if isinstance(api_key, str) and "api_key" in type(block).model_fields:
                    block.api_key = api_key.strip()
                model = values.get("model")
                if isinstance(model, str) and model.strip():
                    block.default_model = model.strip()
                if meta["supports_base_url"]:
                    base_url = values.get("base_url")
                    if isinstance(base_url, str) and base_url.strip():
                        block.base_url = base_url.strip()
                enabled = values.get("enabled")
                if enabled is not None and hasattr(block, "enabled"):
                    block.enabled = bool(enabled)

        primary = stored.get("primary_provider")
        if isinstance(primary, str):
            try:
                self.settings.llm_primary_provider = LLMProvider(primary)
            except ValueError as exc:
                raise ValidationError(f"Proveedor primario inválido: {primary}") from exc
        fallbacks = stored.get("fallback_providers")
        if isinstance(fallbacks, list):
            try:
                self.settings.llm_fallback_providers = [LLMProvider(item) for item in fallbacks]
            except ValueError as exc:
                raise ValidationError(f"Proveedor de fallback inválido: {exc}") from exc
        fallback_enabled = stored.get("fallback_enabled")
        if fallback_enabled is not None:
            self.settings.llm_fallback_enabled = bool(fallback_enabled)
        chain = stored.get("model_chain")
        if isinstance(chain, list):
            try:
                self.settings.llm_model_chain = [
                    LLMChainEntry.model_validate(item) for item in chain
                ]
            except ValueError as exc:
                raise ValidationError(f"Cadena de modelos inválida: {exc}") from exc

    def _apply_telegram(self, stored: dict[str, Any]) -> None:
        token = stored.get("bot_token")
        if isinstance(token, str):
            self.settings.telegram_bot_token = token
        chat_id = stored.get("chat_id")
        if isinstance(chat_id, str):
            self.settings.telegram_chat_id = chat_id
        enabled = stored.get("enabled")
        if enabled is not None:
            self.settings.alerts_enabled = bool(enabled)

    def _apply_heartbeat(self, stored: dict[str, Any]) -> None:
        url = stored.get("ping_url")
        if isinstance(url, str):
            self.settings.heartbeat_ping_url = url
        interval = stored.get("interval_seconds")
        if interval is not None:
            self.settings.heartbeat_interval_seconds = max(15, int(interval))

    # ------------------------------------------------------------------
    # Connectivity tests (read-only)
    # ------------------------------------------------------------------
    async def test_llm(self, provider: LLMProvider | None = None) -> dict[str, Any]:
        gateway = create_llm_gateway(self.settings)
        if gateway is None:
            return {
                "mode": "deterministic_fallback",
                "providers": {},
                "cooldowns": [],
                "message": "Ningún proveedor tiene credenciales configuradas.",
            }
        health = await gateway.check_providers_health()
        if provider is not None:
            health = {provider.value: health.get(provider.value, False)}
        return {"mode": "llm_with_fallback", "providers": health, "cooldowns": []}

    async def list_models(
        self,
        provider: LLMProvider,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> list[LLMModelOption]:
        """Models the provider offers right now (free ones first).

        Free model ids change often, so they are read live instead of being
        hard-coded. OpenRouter's catalogue is public; OpenAI-compatible
        providers (Groq, DeepSeek, OpenAI) need the stored key.
        """
        meta = PROVIDER_META[provider]
        block = getattr(self.settings, meta["attr"])
        if provider == LLMProvider.OPENROUTER:
            base = str(getattr(block, "base_url", "") or meta["default_base_url"])
            headers: dict[str, str] = {}
        elif provider in {LLMProvider.GROQ, LLMProvider.DEEPSEEK, LLMProvider.OPENAI}:
            key = str(getattr(block, "api_key", "") or "").strip()
            if not key:
                raise ValidationError(f"Guarda primero la clave de {meta['label']}.")
            base = str(getattr(block, "base_url", "") or meta["default_base_url"] or "")
            base = base or "https://api.openai.com/v1"
            headers = {"Authorization": f"Bearer {key}"}
        else:
            return []
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
                response = await client.get(f"{base.rstrip('/')}/models", headers=headers)
                response.raise_for_status()
                rows = response.json().get("data", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise ValidationError(f"No se pudo leer la lista de modelos: {exc}") from exc

        options: list[LLMModelOption] = []
        for row in rows:
            model_id = str(row.get("id") or "")
            if not model_id or row.get("active") is False:
                continue
            pricing = row.get("pricing") or {}
            if provider == LLMProvider.OPENROUTER:
                free = model_id.endswith(":free") or (
                    str(pricing.get("prompt")) == "0" and str(pricing.get("completion")) == "0"
                )
            else:
                # Groq's own API is free-tier for every model; others are paid.
                free = provider == LLMProvider.GROQ
            # Audio, music and moderation models cannot act as analysts.
            outputs = (row.get("architecture") or {}).get("output_modalities")
            if isinstance(outputs, list) and "text" not in outputs:
                continue
            if any(tag in model_id for tag in _NON_CHAT_MODEL_TAGS):
                continue
            options.append(
                LLMModelOption(
                    id=model_id,
                    name=str(row.get("name") or model_id),
                    free=free,
                    context_length=row.get("context_length") or row.get("context_window"),
                )
            )
        options.sort(key=lambda option: (not option.free, option.id))
        return options

    async def test_heartbeat(self) -> bool:
        from src.application.services.heartbeat_service import HeartbeatService

        url = (self.settings.heartbeat_ping_url or "").strip()
        if not url:
            return False
        service = HeartbeatService(
            url=url,
            interval_seconds=int(self.settings.heartbeat_interval_seconds),
        )
        return await service.ping_once()


def apply_persisted_integration_settings(settings: Settings | None = None) -> None:
    """Load the encrypted store and apply it to the cached settings instance.

    Called once during application startup, before the LLM gateway and alert
    service are built.
    """
    try:
        service = IntegrationSettingsService(settings=settings or get_settings())
        service.apply_persisted()
    except Exception as exc:  # pragma: no cover - startup must not crash on a bad store
        logger.warning("integration_settings_apply_failed", error=str(exc))
