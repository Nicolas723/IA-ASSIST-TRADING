"""
MACD Momentum Strategy Plugin.
Trades breakouts when the MACD histogram expands positively, and exits when momentum reverses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.indicators.builtin import MACDIndicator
from src.domain.strategy.base import BaseStrategy
from src.domain.strategy.entities import Signal, SignalSide, StrategyMetadata

if TYPE_CHECKING:
    from src.domain.market.entities import Candle, MarketContext


class MACDMomentumStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        name="macd_momentum",
        version="1.0.0",
        author="AI Trading Team",
        description="Momentum trend strategy entering long on MACD bullish zero-line crossovers and histogram expansions.",
        risk_level="medium",
        default_parameters={
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "stop_loss_pct": 3.0,
            "take_profit_pct": 7.0,
        },
    )

    def __init__(self, parameters: dict[str, Any] | None = None) -> None:
        super().__init__(parameters)
        fast = int(self.parameters.get("fast_period", 12))
        slow = int(self.parameters.get("slow_period", 26))
        sig = int(self.parameters.get("signal_period", 9))
        self.macd_indicator = MACDIndicator(fast_period=fast, slow_period=slow, signal_period=sig)

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal:
        slow_period = int(self.parameters.get("slow_period", 26))
        sig_period = int(self.parameters.get("signal_period", 9))
        if len(history) < slow_period + sig_period + 2:
            return Signal(
                symbol=candle.symbol, timeframe=candle.timeframe.value, side=SignalSide.HOLD
            )

        curr_res = self.macd_indicator.calculate(history)
        prev_res = self.macd_indicator.calculate(history[:-1])

        curr_hist = curr_res.values.get("histogram")
        prev_hist = prev_res.values.get("histogram")
        current_price = float(candle.close)

        sl_pct = float(self.parameters.get("stop_loss_pct", 3.0)) / 100.0
        tp_pct = float(self.parameters.get("take_profit_pct", 7.0)) / 100.0

        if curr_hist is not None and prev_hist is not None:
            # Bullish flip
            if prev_hist <= 0 and curr_hist > 0:
                return Signal(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe.value,
                    side=SignalSide.BUY,
                    confidence=0.88,
                    entry_price=current_price,
                    stop_loss=current_price * (1.0 - sl_pct),
                    take_profit=current_price * (1.0 + tp_pct),
                    reason="MACD histogram flipped positive (Bullish momentum breakout)",
                    indicators_snapshot=curr_res.values,
                )
            # Bearish flip
            elif prev_hist >= 0 and curr_hist < 0:
                return Signal(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe.value,
                    side=SignalSide.SELL,
                    confidence=0.88,
                    entry_price=current_price,
                    reason="MACD histogram flipped negative (Bearish momentum exhaustion)",
                    indicators_snapshot=curr_res.values,
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
