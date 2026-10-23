"""
EMA Crossover Strategy Plugin (Trend-Following).
Enters Long on Bullish EMA Crossover (Fast EMA > Slow EMA) and exits on Bearish Crossover.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.indicators.builtin import EMASuiteIndicator
from src.domain.strategy.base import BaseStrategy
from src.domain.strategy.entities import Signal, SignalSide, StrategyMetadata

if TYPE_CHECKING:
    from src.domain.market.entities import Candle, MarketContext


class EMACrossoverStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        name="ema_crossover",
        version="1.0.0",
        author="AI Trading Team",
        description="Trend following system executing long positions on fast EMA (9) crossover above slow EMA (21).",
        risk_level="low",
        default_parameters={
            "fast_ema": 9,
            "slow_ema": 21,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 6.0,
        },
    )

    def __init__(self, parameters: dict[str, Any] | None = None) -> None:
        super().__init__(parameters)
        fast = int(self.parameters.get("fast_ema", 9))
        slow = int(self.parameters.get("slow_ema", 21))
        self.ema_indicator = EMASuiteIndicator(periods=[fast, slow])

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal:
        slow_period = int(self.parameters.get("slow_ema", 21))
        if len(history) < slow_period + 2:
            return Signal(
                symbol=candle.symbol, timeframe=candle.timeframe.value, side=SignalSide.HOLD
            )

        current_res = self.ema_indicator.calculate(history)
        prev_res = self.ema_indicator.calculate(history[:-1])

        fast_k = f"ema_{self.parameters.get('fast_ema', 9)}"
        slow_k = f"ema_{self.parameters.get('slow_ema', 21)}"

        c_fast = current_res.values.get(fast_k)
        c_slow = current_res.values.get(slow_k)
        p_fast = prev_res.values.get(fast_k)
        p_slow = prev_res.values.get(slow_k)

        current_price = float(candle.close)
        sl_pct = float(self.parameters.get("stop_loss_pct", 2.0)) / 100.0
        tp_pct = float(self.parameters.get("take_profit_pct", 6.0)) / 100.0

        if c_fast and c_slow and p_fast and p_slow:
            # Bullish Golden Crossover: Fast crosses above Slow
            if p_fast <= p_slow and c_fast > c_slow:
                return Signal(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe.value,
                    side=SignalSide.BUY,
                    confidence=0.90,
                    entry_price=current_price,
                    stop_loss=current_price * (1.0 - sl_pct),
                    take_profit=current_price * (1.0 + tp_pct),
                    reason=f"Bullish EMA Crossover ({fast_k} crossed above {slow_k})",
                    indicators_snapshot={fast_k: c_fast, slow_k: c_slow},
                )
            # Bearish Death Crossover: Fast crosses below Slow
            elif p_fast >= p_slow and c_fast < c_slow:
                return Signal(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe.value,
                    side=SignalSide.SELL,
                    confidence=0.90,
                    entry_price=current_price,
                    reason=f"Bearish EMA Crossover ({fast_k} crossed below {slow_k})",
                    indicators_snapshot={fast_k: c_fast, slow_k: c_slow},
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
