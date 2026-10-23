"""Risk Management API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.application.services.risk_service import RiskService
from src.domain.risk.entities import (
    OrderRiskCheckRequest,
    OrderRiskCheckResult,
    PositionSizeRequest,
    PositionSizeResult,
    RiskProfile,
    RiskStatusSummary,
)
from src.interfaces.api.v1.deps import get_risk_service

router = APIRouter()


class TripRequest(BaseModel):
    reason: str = "Manual Emergency Kill-Switch activated from UI"


@router.get(
    "/status",
    response_model=RiskStatusSummary,
    summary="Get comprehensive risk and circuit breaker status",
)
async def get_risk_status(
    service: RiskService = Depends(get_risk_service),
) -> RiskStatusSummary:
    """Return live circuit breaker state, drawdown meters, and active risk limits."""
    return service.get_status()


@router.post(
    "/validate-order",
    response_model=OrderRiskCheckResult,
    summary="Pre-flight order risk validation",
)
async def validate_order(
    request: OrderRiskCheckRequest,
    service: RiskService = Depends(get_risk_service),
) -> OrderRiskCheckResult:
    """
    Evaluate proposed order against multi-level circuit breakers, portfolio limits,
    mandatory stop-loss enforcement, and maximum risk allocations.
    """
    return service.validate_order(request)


@router.post(
    "/calculate-size",
    response_model=PositionSizeResult,
    summary="Calculate optimal position sizing",
)
async def calculate_position_size(
    request: PositionSizeRequest,
    service: RiskService = Depends(get_risk_service),
) -> PositionSizeResult:
    """Calculate mathematically sound position size based on exact price risk and account capital."""
    return service.calculate_position_size(request)


@router.post(
    "/circuit-breaker/trip",
    response_model=RiskStatusSummary,
    summary="Trip the emergency circuit breaker",
)
async def trip_circuit_breaker(
    request: TripRequest | None = None,
    service: RiskService = Depends(get_risk_service),
) -> RiskStatusSummary:
    """Emergency halt all trading activities across the entire assistant."""
    reason = request.reason if request else "Manual Emergency Kill-Switch activated"
    return service.trip_circuit_breaker(reason)


@router.post(
    "/circuit-breaker/reset",
    response_model=RiskStatusSummary,
    summary="Reset and re-arm circuit breaker",
)
async def reset_circuit_breaker(
    service: RiskService = Depends(get_risk_service),
) -> RiskStatusSummary:
    """Reset the circuit breaker and resume normal trading operations."""
    return service.reset_circuit_breaker()


@router.put(
    "/profile", response_model=RiskStatusSummary, summary="Update active risk management profile"
)
async def update_risk_profile(
    profile: RiskProfile,
    service: RiskService = Depends(get_risk_service),
) -> RiskStatusSummary:
    """Update risk limits (Max daily loss, Max drawdown, Max risk per trade)."""
    return service.update_profile(profile)
