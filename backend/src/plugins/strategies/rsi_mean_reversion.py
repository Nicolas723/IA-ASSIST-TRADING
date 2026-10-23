"""
RSI Mean Reversion Strategy Plugin.
Buys when RSI drops below oversold threshold and recovers; exits at overbought.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.indicators.builtin import RSIIndicator
from src.domain.strategy.base import BaseStrategy
from src.domain.strategy.entities import Signal, SignalSide, StrategyMetadata

if TYPE_CHECKING:
    from src.domain.market.entities import Candle, MarketContext


class RSIMeanReversionStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        name="rsi_mean_reversion",
        version="1.0.0",
        author="AI Trading Team",
        description="Mean reversion strategy entering long on RSI oversold pullbacks and selling on overbought peaks.",
        risk_level="medium",
        default_parameters={
            "rsi_period": 14,
            "oversold": 30.0,
            "overbought": 70.0,
            "stop_loss_pct": 2.5,
            "take_profit_pct": 5.0,
        },
    )

    def __init__(self, parameters: dict[str, Any] | None = None) -> None:
        super().__init__(parameters)
        self.rsi_indicator = RSIIndicator(
            period=int(self.parameters.get("rsi_period", 14)),
            oversold=float(self.parameters.get("oversold", 30.0)),
            overbought=float(self.parameters.get("overbought", 70.0)),
        )

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal:
        if len(history) < int(self.parameters.get("rsi_period", 14)) + 2:
            return Signal(
                symbol=candle.symbol, timeframe=candle.timeframe.value, side=SignalSide.HOLD
            )

        res = self.rsi_indicator.calculate(history)
        rsi_val = res.values.get("rsi")
        current_price = float(candle.close)

        if rsi_val is None:
            return Signal(
                symbol=candle.symbol, timeframe=candle.timeframe.value, side=SignalSide.HOLD
            )

        sl_pct = float(self.parameters.get("stop_loss_pct", 2.5)) / 100.0
        tp_pct = float(self.parameters.get("take_profit_pct", 5.0)) / 100.0

        if rsi_val <= float(self.parameters.get("oversold", 30.0)):
            return Signal(
                symbol=candle.symbol,
                timeframe=candle.timeframe.value,
                side=SignalSide.BUY,
                confidence=0.85,
                entry_price=current_price,
                stop_loss=current_price * (1.0 - sl_pct),
                take_profit=current_price * (1.0 + tp_pct),
                reason=f"RSI oversold trigger ({rsi_val} <= {self.parameters['oversold']})",
                indicators_snapshot={"rsi": rsi_val},
            )
        elif rsi_val >= float(self.parameters.get("overbought", 70.0)):
            return Signal(
                symbol=candle.symbol,
                timeframe=candle.timeframe.value,
                side=SignalSide.SELL,
                confidence=0.85,
                entry_price=current_price,
                reason=f"RSI overbought exit trigger ({rsi_val} >= {self.parameters['overbought']})",
                indicators_snapshot={"rsi": rsi_val},
            )

        return Signal(symbol=candle.symbol, timeframe=candle.timeframe.value, side=SignalSide.HOLD)

    async def generate_signal(self, context: MarketContext | Any) -> Signal:
        candles = getattr(context, "candles", {})
        if isinstance(candles, dict):
            first_key = next(iter(candles.keys()), "1h")
            candle_list = candles.get(first_key, [])
        elif isinstance(candles, list):
            candle_list = candles
        else:
            candle_list = []

        if not candle_list:
            return Signal(side=SignalSide.HOLD, reason="No candles in context")

        return self.on_candle(candle_list[-1], candle_list)
