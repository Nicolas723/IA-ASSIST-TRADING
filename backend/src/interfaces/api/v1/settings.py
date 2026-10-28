"""UI-managed integration settings API.

These endpoints replace editing ``backend/.env`` for LLM provider keys, Telegram
alerts and the outbound heartbeat. Secrets are write-only: the GET response only
returns masked values.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.application.services.alert_service import AlertService, AlertStatus
from src.application.services.integration_settings_service import (
    HeartbeatSettingsUpdate,
    IntegrationSettingsService,
    IntegrationSettingsView,
    LLMModelOption,
    LLMSettingsUpdate,
    TelegramSettingsUpdate,
)
from src.domain.notifications.entities import AlertSeverity
from src.interfaces.api.v1.deps import (
    get_alert_service,
    get_integration_settings_service,
    refresh_alert_service,
    refresh_llm_gateway,
)
from src.shared.config.settings import LLMProvider
from src.shared.exceptions.domain import ValidationError

router = APIRouter()


class LLMTestRequest(BaseModel):
    provider: LLMProvider | None = Field(
        default=None,
        description="Test one provider, or every configured provider when omitted.",
    )


class TelegramTestResponse(BaseModel):
    delivered: bool
    status: AlertStatus


class HeartbeatTestResponse(BaseModel):
    ok: bool


@router.get(
    "/integrations",
    response_model=IntegrationSettingsView,
    summary="Show masked LLM, Telegram and heartbeat settings",
)
async def get_integration_settings(
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> IntegrationSettingsView:
    """Return the effective configuration with every secret masked."""
    return service.snapshot()


@router.put(
    "/llm",
    response_model=IntegrationSettingsView,
    summary="Save LLM provider credentials and routing",
)
async def update_llm_settings(
    payload: LLMSettingsUpdate,
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> IntegrationSettingsView:
    """Persist provider keys/models and rebuild the gateway immediately."""
    try:
        service.update_llm(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refresh_llm_gateway()
    return service.snapshot()


@router.get(
    "/llm/models/{provider}",
    response_model=list[LLMModelOption],
    summary="List the models a provider offers (free ones first)",
)
async def list_llm_models(
    provider: LLMProvider,
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> list[LLMModelOption]:
    """Read the provider's live catalogue; nothing is sent besides the stored key."""
    try:
        return await service.list_models(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/llm/test",
    summary="Check LLM provider connectivity (read-only)",
)
async def test_llm_providers(
    payload: LLMTestRequest | None = None,
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> dict[str, object]:
    """Run the provider health check without sending market or trading data."""
    body = payload or LLMTestRequest()
    return await service.test_llm(body.provider)


@router.put(
    "/telegram",
    response_model=IntegrationSettingsView,
    summary="Save Telegram bot token and chat id",
)
async def update_telegram_settings(
    payload: TelegramSettingsUpdate,
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> IntegrationSettingsView:
    """Persist Telegram credentials and reload the alert channels in place."""
    try:
        service.update_telegram(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refresh_alert_service()
    return service.snapshot()


@router.post(
    "/telegram/test",
    response_model=TelegramTestResponse,
    summary="Send a test alert through the configured channels",
)
async def test_telegram(
    alert_service: AlertService = Depends(get_alert_service),
) -> TelegramTestResponse:
    delivered = await alert_service.notify(
        title="Prueba de alertas",
        message="Telegram está conectado. Aquí llegarán las compras y los cierres del asistente.",
        severity=AlertSeverity.INFO,
        metadata={"event": "test"},  # never suppressed as a repeat
    )
    return TelegramTestResponse(delivered=delivered, status=alert_service.status())


@router.put(
    "/heartbeat",
    response_model=IntegrationSettingsView,
    summary="Save the outbound heartbeat URL and interval",
)
async def update_heartbeat_settings(
    payload: HeartbeatSettingsUpdate,
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> IntegrationSettingsView:
    """Persist the heartbeat and restart its background loop without a restart."""
    try:
        await service.update_heartbeat(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.snapshot()


@router.post(
    "/heartbeat/test",
    response_model=HeartbeatTestResponse,
    summary="Send one heartbeat ping now",
)
async def test_heartbeat(
    service: IntegrationSettingsService = Depends(get_integration_settings_service),
) -> HeartbeatTestResponse:
    return HeartbeatTestResponse(ok=await service.test_heartbeat())
