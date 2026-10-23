"""
Risk Manager AI Specialist Agent.
Evaluates portfolio exposure, volatility expansion, risk-to-reward viability, and sets strict boundaries.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from src.domain.agents.base import BaseAgent
from src.domain.agents.entities import (
    AgentOutput,
    AgentStatus,
    AgentTask,
    AgentType,
    KeyFactor,
    PriceTarget,
    RiskFlag,
    RiskFlagSeverity,
    SignalDirection,
)
from src.domain.indicators.builtin import ATRIndicator
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.infrastructure.llm.gateway import LLMGateway

logger = get_logger(__name__)


class RiskManagerAgent(BaseAgent):
    """
    Agent responsible for safeguarding capital, assessing trade viability, and setting stop/profit levels.
    """

    def __init__(self, llm_gateway: LLMGateway | None = None) -> None:
        self.llm = llm_gateway
        self.atr_ind = ATRIndicator()

    @property
    def agent_type(self) -> AgentType:
        return AgentType.RISK_MANAGER

    async def analyze(self, task: AgentTask) -> AgentOutput:
        return await self.fallback_analyze(task)

    async def fallback_analyze(self, task: AgentTask) -> AgentOutput:
        candles = task.market_context.candles.get("1h", [])
        if not candles:
            candles = next(iter(task.market_context.candles.values()), [])

        if not candles:
            return AgentOutput(
                task_id=task.task_id,
                agent_type=self.agent_type,
                symbol=task.symbol,
                signal=SignalDirection.HOLD,
                confidence=1.0,
                reasoning="Risk Manager: No candle data available to assess risk.",
                status=AgentStatus.DONE,
                is_fallback=True,
            )

        current_price = float(candles[-1].close)
        atr_res = self.atr_ind.calculate(candles)
        atr_val = atr_res.values.get("atr") or (current_price * 0.02)
        atr_pct = atr_res.values.get("atr_percent") or 2.0

        risk_flags: list[RiskFlag] = []
        factors: list[KeyFactor] = []

        # Check volatility
        if atr_pct > 5.0:
            risk_flags.append(
                RiskFlag(
                    code="EXTREME_VOLATILITY",
                    description=f"Hourly ATR is {atr_pct}% of price (high volatility expansion).",
                    severity=RiskFlagSeverity.HIGH,
                    suggested_action="Reduce standard position size by 50%.",
                )
            )
        elif atr_pct < 0.5:
            risk_flags.append(
                RiskFlag(
                    code="LOW_VOLATILITY_COMPRESSION",
                    description=f"Hourly ATR is {atr_pct}% of price (extreme squeeze).",
                    severity=RiskFlagSeverity.LOW,
                    suggested_action="Prepare for breakout volatility expansion.",
                )
            )

        factors.append(
            KeyFactor(
                name="Volatility (ATR)",
                impact="neutral" if atr_pct <= 4.0 else "bearish",
                weight=0.9,
                description=f"14-period ATR is ${atr_val:.2f} ({atr_pct:.2f}%).",
            )
        )

        stop_loss = round(current_price - (atr_val * 1.8), 2)
        take_profit = round(current_price + (atr_val * 3.6), 2)
        rr_ratio = Decimal("2.0")

        targets = PriceTarget(
            entry=Decimal(str(current_price)),
            stop_loss=Decimal(str(stop_loss)),
            take_profit_1=Decimal(str(take_profit)),
            risk_reward_ratio=rr_ratio,
        )

        reasoning = (
            f"Risk Management Assessment for {task.symbol}: "
            f"Current ATR (${atr_val:.2f}) supports a dynamic stop loss at ${stop_loss:,.2f} "
            f"and target at ${take_profit:,.2f} achieving a strict 2.0:1 Risk/Reward ratio. "
            f"Max capital risk recommended: 1.5%."
        )

        return AgentOutput(
            task_id=task.task_id,
            agent_type=self.agent_type,
            symbol=task.symbol,
            signal=SignalDirection.BUY
            if not risk_flags or risk_flags[0].severity != RiskFlagSeverity.CRITICAL
            else SignalDirection.HOLD,
            confidence=0.85,
            reasoning=reasoning,
            key_factors=factors,
            risk_flags=risk_flags,
            price_targets=targets,
            status=AgentStatus.DONE,
            is_fallback=True,
        )
