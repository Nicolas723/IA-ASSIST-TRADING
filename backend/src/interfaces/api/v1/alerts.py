"""Alerting API Router — v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.application.services.alert_service import AlertService, AlertStatus
from src.domain.notifications.entities import AlertSeverity
from src.interfaces.api.v1.deps import get_alert_service

router = APIRouter()


class TestAlertBody(BaseModel):
    title: str = Field(default="Prueba de alertas", max_length=120)
    message: str = Field(
        default="Si recibes este mensaje, las notificaciones están configuradas correctamente.",
        max_length=2000,
    )
    severity: AlertSeverity = AlertSeverity.INFO


class TestAlertResponse(BaseModel):
    delivered: bool
    status: AlertStatus


@router.get("/status", response_model=AlertStatus, summary="Get alert channel status")
async def get_alert_status(
    service: AlertService = Depends(get_alert_service),
) -> AlertStatus:
    """Return which notification channels are configured and their counters."""
    return service.status()


@router.post("/test", response_model=TestAlertResponse, summary="Send a test alert")
async def send_test_alert(
    body: TestAlertBody | None = None,
    service: AlertService = Depends(get_alert_service),
) -> TestAlertResponse:
    """Deliver a test notification through every configured channel."""
    payload = body or TestAlertBody()
    delivered = await service.notify(
        title=payload.title,
        message=payload.message,
        severity=payload.severity,
    )
    return TestAlertResponse(delivered=delivered, status=service.status())
