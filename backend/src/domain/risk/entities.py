"""
Domain Entities and Value Objects for Risk Management and Circuit Breakers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CircuitBreakerState(StrEnum):
    ARMED = "armed"  # Active monitoring, trading allowed
    TRIPPED = "tripped"  # Circuit broken, all trading halted
    COOLDOWN = "cooldown"  # Waiting period before re-arming
    DISABLED = "disabled"  # Risk guard manually bypassed (testing only)


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskProfile(BaseModel):
    max_daily_loss_pct: float = Field(
        default=3.0, description="Max daily loss before trading halt (%)"
    )
    max_drawdown_pct: float = Field(
        default=10.0, description="Max portfolio drawdown before circuit trip (%)"
    )
    max_risk_per_trade_pct: float = Field(
        default=1.5, description="Max risk per trade as % of capital"
    )
    max_position_size_pct: float = Field(
        default=20.0, description="Max capital allocation per single asset (%)"
    )
    max_portfolio_exposure_pct: float = Field(
        default=100.0, description="Max gross portfolio exposure including the proposed order (%)"
    )
    max_open_positions: int = Field(default=5, description="Max concurrent open positions")
    enforce_stop_loss: bool = Field(
        default=True, description="Require valid stop-loss on every order"
    )
    min_risk_reward_ratio: float = Field(
        default=1.5, description="Minimum acceptable risk/reward ratio"
    )


class OrderRiskCheckRequest(BaseModel):
    symbol: str
    side: str
    order_type: str = "market"
    price: float
    quantity: float
    stop_loss: float | None = None
    take_profit: float | None = None
    account_balance: float = 10000.0
    current_open_positions_count: int = 0
    current_total_exposure_usd: float = 0.0
    current_symbol_exposure_usd: float = 0.0
    current_daily_loss_pct: float = 0.0
    current_drawdown_pct: float = 0.0


class OrderRiskCheckResult(BaseModel):
    is_allowed: bool
    risk_level: RiskLevel
    violation_reasons: list[str] = Field(default_factory=list)
    calculated_risk_usd: float = 0.0
    risk_pct_of_balance: float = 0.0
    risk_reward_ratio: float | None = None
    max_allowed_quantity: float
    recommended_quantity: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class PositionSizeRequest(BaseModel):
    symbol: str = "BTC/USDT"
    account_balance: float = 10000.0
    risk_percentage: float = 1.5  # 1.5% of account balance
    entry_price: float = 65000.0
    stop_loss_price: float = 63500.0
    take_profit_price: float | None = 68000.0


class PositionSizeResult(BaseModel):
    symbol: str
    account_balance: float
    risk_percentage: float
    risk_amount_usd: float
    price_risk_per_unit: float
    price_risk_pct: float
    recommended_units: float
    recommended_notional_usd: float
    risk_reward_ratio: float | None
    potential_profit_usd: float | None
    is_valid_setup: bool
    notes: str


class RiskStatusSummary(BaseModel):
    circuit_breaker_state: CircuitBreakerState
    daily_loss_pct: float
    max_daily_loss_pct: float
    current_drawdown_pct: float
    max_drawdown_pct: float
    open_positions_count: int
    max_open_positions: int
    current_equity_usd: float = 0.0
    gross_exposure_usd: float = 0.0
    max_portfolio_exposure_pct: float = 100.0
    real_trading_enabled: bool
    kill_switch_active: bool
    profile: RiskProfile
    last_trip_reason: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
