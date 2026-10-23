"""Observability & MLOps API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.application.services.observability_service import (
    MLOpsTelemetrySnapshot,
    ObservabilityService,
    SystemHealthSnapshot,
)
from src.interfaces.api.v1.deps import get_observability_service

router = APIRouter()


@router.get(
    "/mlops",
    response_model=MLOpsTelemetrySnapshot,
    summary="Get MLOps telemetry & model drift metrics",
)
async def get_mlops_telemetry(
    service: ObservabilityService = Depends(get_observability_service),
) -> MLOpsTelemetrySnapshot:
    """Retrieve LLM token expenditures, dollar costs, specialist agent latencies, and market regime tracking."""
    return service.get_mlops_snapshot()


@router.get(
    "/health", response_model=SystemHealthSnapshot, summary="Get deep production system health"
)
async def get_deep_health(
    service: ObservabilityService = Depends(get_observability_service),
) -> SystemHealthSnapshot:
    """Retrieve production telemetry including uptime, memory usage, TimescaleDB pool, Redis cache, and Celery status."""
    return service.get_system_health()
