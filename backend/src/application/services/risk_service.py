"""
RiskService — Application Layer for Risk Monitoring, Order Pre-Flight Validation, and Circuit Breakers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.domain.risk.drawdown_guard import DrawdownGuard
from src.domain.risk.entities import (
    OrderRiskCheckRequest,
    OrderRiskCheckResult,
    PositionSizeRequest,
    PositionSizeResult,
    RiskProfile,
    RiskStatusSummary,
)
from src.domain.risk.position_sizer import PositionSizer
from src.shared.config.settings import get_settings
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class RiskService:
    """
    Coordinates real-time risk checks, circuit breaker interventions, and position sizing.
    """

    def __init__(self, guard: DrawdownGuard | None = None) -> None:
        settings = get_settings()
        initial_profile = RiskProfile(
            max_daily_loss_pct=float(settings.risk.daily_loss_pct),
            max_drawdown_pct=float(settings.risk.max_drawdown_pct),
            max_risk_per_trade_pct=float(settings.risk.max_risk_per_trade_pct),
            max_position_size_pct=float(settings.risk.position_size_pct),
            max_portfolio_exposure_pct=float(settings.risk.portfolio_exposure_pct),
            max_open_positions=int(settings.risk.max_open_positions),
        )
        self.guard = guard or DrawdownGuard(profile=initial_profile)
        self.current_daily_loss_pct: float = 0.0
        self.current_drawdown_pct: float = 0.0
        self.open_positions_count: int = 0
        self.current_equity_usd: float = 0.0
        self.current_gross_exposure_usd: float = 0.0

    def get_status(self) -> RiskStatusSummary:
        settings = get_settings()
        return RiskStatusSummary(
            circuit_breaker_state=self.guard.state,
            daily_loss_pct=self.current_daily_loss_pct,
            max_daily_loss_pct=self.guard.profile.max_daily_loss_pct,
            current_drawdown_pct=self.current_drawdown_pct,
            max_drawdown_pct=self.guard.profile.max_drawdown_pct,
            open_positions_count=self.open_positions_count,
            max_open_positions=self.guard.profile.max_open_positions,
            current_equity_usd=self.current_equity_usd,
            gross_exposure_usd=self.current_gross_exposure_usd,
            max_portfolio_exposure_pct=self.guard.profile.max_portfolio_exposure_pct,
            real_trading_enabled=settings.real_trading_enabled,
            kill_switch_active=self.guard.kill_switch_active,
            profile=self.guard.profile,
            last_trip_reason=self.guard.last_trip_reason,
            updated_at=datetime.now(tz=UTC),
        )

    def validate_order(self, request: OrderRiskCheckRequest) -> OrderRiskCheckResult:
        # Merge independently observed state conservatively: application-level
        # portfolio values must never be overwritten by an unrefreshed (or
        # lower) RiskService cache and accidentally weaken the guard.
        request.current_daily_loss_pct = max(
            request.current_daily_loss_pct, self.current_daily_loss_pct
        )
        request.current_drawdown_pct = max(request.current_drawdown_pct, self.current_drawdown_pct)
        request.current_open_positions_count = max(
            request.current_open_positions_count, self.open_positions_count
        )
        return self.guard.validate_order(request)

    def calculate_position_size(self, request: PositionSizeRequest) -> PositionSizeResult:
        return PositionSizer.calculate(request, self.guard.profile)

    def trip_circuit_breaker(
        self, reason: str = "Manual Emergency Halt triggered by user"
    ) -> RiskStatusSummary:
        self.guard.trip(reason)
        return self.get_status()

    def reset_circuit_breaker(self) -> RiskStatusSummary:
        self.guard.reset()
        return self.get_status()

    def update_profile(self, new_profile: RiskProfile) -> RiskStatusSummary:
        self.guard.profile = new_profile
        logger.info("risk_profile_updated", profile=new_profile.model_dump())
        return self.get_status()
