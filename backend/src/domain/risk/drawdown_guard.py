"""
DrawdownGuard and Multi-Level Circuit Breaker System.
"""

from __future__ import annotations

from src.domain.risk.entities import (
    CircuitBreakerState,
    OrderRiskCheckRequest,
    OrderRiskCheckResult,
    RiskLevel,
    RiskProfile,
)
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class DrawdownGuard:
    """
    Guards the trading system against catastrophic losses and out-of-control drawdowns.
    """

    def __init__(self, profile: RiskProfile | None = None) -> None:
        self.profile = profile or RiskProfile()
        self.state: CircuitBreakerState = CircuitBreakerState.ARMED
        self.last_trip_reason: str | None = None
        self.kill_switch_active: bool = False

    def trip(self, reason: str) -> None:
        """Manually or automatically trip the circuit breaker."""
        self.state = CircuitBreakerState.TRIPPED
        self.last_trip_reason = reason
        self.kill_switch_active = True
        logger.critical("circuit_breaker_tripped", reason=reason)

    def reset(self) -> None:
        """Reset and re-arm the circuit breaker."""
        self.state = CircuitBreakerState.ARMED
        self.last_trip_reason = None
        self.kill_switch_active = False
        logger.info("circuit_breaker_reset_armed")

    def validate_order(self, req: OrderRiskCheckRequest) -> OrderRiskCheckResult:
        """
        Validate proposed order against risk profile and active circuit breaker states.
        """
        violations: list[str] = []
        risk_level = RiskLevel.LOW

        # 1. Circuit Breaker Check
        if self.state == CircuitBreakerState.TRIPPED or self.kill_switch_active:
            violations.append(
                f"Circuit Breaker is TRIPPED. Reason: {self.last_trip_reason or 'Emergency Kill-Switch active'}"
            )
            return OrderRiskCheckResult(
                is_allowed=False,
                risk_level=RiskLevel.CRITICAL,
                violation_reasons=violations,
                max_allowed_quantity=0.0,
                recommended_quantity=0.0,
            )

        # 2. Portfolio Drawdown Check (Level 3 Breaker)
        if req.current_drawdown_pct >= self.profile.max_drawdown_pct:
            self.trip(
                f"Portfolio drawdown ({req.current_drawdown_pct}%) reached limit ({self.profile.max_drawdown_pct}%)"
            )
            violations.append(
                f"Max drawdown limit exceeded ({req.current_drawdown_pct}% >= {self.profile.max_drawdown_pct}%)"
            )
            return OrderRiskCheckResult(
                is_allowed=False,
                risk_level=RiskLevel.CRITICAL,
                violation_reasons=violations,
                max_allowed_quantity=0.0,
                recommended_quantity=0.0,
            )

        # 3. Daily Loss Limit Check (Level 2 Breaker)
        if req.current_daily_loss_pct >= self.profile.max_daily_loss_pct:
            violations.append(
                f"Daily loss limit reached ({req.current_daily_loss_pct}% >= {self.profile.max_daily_loss_pct}%). Trading suspended for today."
            )
            risk_level = RiskLevel.HIGH

        # 4. Max Concurrent Positions Check
        if (
            req.side.upper() == "BUY"
            and req.current_open_positions_count >= self.profile.max_open_positions
        ):
            violations.append(
                f"Max concurrent open positions limit ({self.profile.max_open_positions}) reached."
            )
            risk_level = RiskLevel.HIGH

        # 5. Stop Loss Enforcement (Level 1 Breaker)
        if self.profile.enforce_stop_loss and (req.stop_loss is None or req.stop_loss <= 0):
            violations.append(
                "Order rejected: Valid Stop-Loss is mandatory under current Risk Profile."
            )
            risk_level = RiskLevel.HIGH

        # 6. Position Size and Risk Allocation Validation
        order_notional = req.price * req.quantity
        max_asset_notional = req.account_balance * (self.profile.max_position_size_pct / 100.0)
        asset_capacity = max(0.0, max_asset_notional - req.current_symbol_exposure_usd)
        portfolio_capacity = max(
            0.0,
            req.account_balance * (self.profile.max_portfolio_exposure_pct / 100.0)
            - req.current_total_exposure_usd,
        )
        max_notional_allowed = (
            min(asset_capacity, portfolio_capacity)
            if req.side.upper() == "BUY"
            else max_asset_notional
        )
        max_allowed_qty = round(max_notional_allowed / req.price, 6) if req.price > 0 else 0.0

        if req.side.upper() == "BUY" and order_notional > asset_capacity:
            violations.append(
                "Proposed order exceeds remaining per-asset allocation "
                f"(${round(asset_capacity, 2)} remaining)."
            )
            risk_level = RiskLevel.MEDIUM
        if req.side.upper() == "BUY" and order_notional > portfolio_capacity:
            violations.append(
                "Proposed order exceeds remaining portfolio exposure capacity "
                f"(${round(portfolio_capacity, 2)} remaining)."
            )
            risk_level = RiskLevel.HIGH

        # Risk USD and Risk/Reward calculation
        calculated_risk_usd = 0.0
        risk_pct_of_balance = 0.0
        rr_ratio: float | None = None

        if req.stop_loss and req.stop_loss > 0:
            price_risk = abs(req.price - req.stop_loss)
            calculated_risk_usd = round(req.quantity * price_risk, 2)
            risk_pct_of_balance = (
                round((calculated_risk_usd / req.account_balance) * 100.0, 2)
                if req.account_balance > 0
                else 0.0
            )

            if risk_pct_of_balance > self.profile.max_risk_per_trade_pct:
                violations.append(
                    f"Risk on trade ({risk_pct_of_balance}%) exceeds max risk per trade limit ({self.profile.max_risk_per_trade_pct}%)."
                )
                risk_level = RiskLevel.HIGH

            if req.take_profit and req.take_profit > 0 and price_risk > 0:
                reward = abs(req.take_profit - req.price)
                rr_ratio = round(reward / price_risk, 2)
                if rr_ratio < self.profile.min_risk_reward_ratio:
                    violations.append(
                        f"Risk/Reward ratio ({rr_ratio}:1) is below required minimum ({self.profile.min_risk_reward_ratio}:1)."
                    )
                    risk_level = RiskLevel.MEDIUM

        is_allowed = len(violations) == 0

        return OrderRiskCheckResult(
            is_allowed=is_allowed,
            risk_level=risk_level,
            violation_reasons=violations,
            calculated_risk_usd=calculated_risk_usd,
            risk_pct_of_balance=risk_pct_of_balance,
            risk_reward_ratio=rr_ratio,
            max_allowed_quantity=max_allowed_qty,
            recommended_quantity=min(req.quantity, max_allowed_qty),
        )
