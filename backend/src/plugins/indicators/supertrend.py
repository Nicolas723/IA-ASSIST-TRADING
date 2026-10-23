"""
Custom Plugin Indicator: SuperTrend.
Demonstrates the plugin architecture for adding custom indicators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.indicators.base import BaseIndicator
from src.domain.indicators.entities import (
    IndicatorCategory,
    IndicatorMetadata,
    IndicatorResult,
    IndicatorSignal,
)

if TYPE_CHECKING:
    from src.domain.market.entities import Candle


class SuperTrendIndicator(BaseIndicator):
    metadata = IndicatorMetadata(
        name="supertrend",
        display_name="SuperTrend Indicator",
        category=IndicatorCategory.TREND,
        description="Volatility-based trend following indicator combining ATR and median price.",
        parameters={"period": 10, "multiplier": 3.0},
    )

    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        self.period = period
        self.multiplier = multiplier

    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        if len(candles) < self.period + 1:
            return IndicatorResult(
                name="supertrend",
                category=IndicatorCategory.TREND,
                values={"supertrend": None},
                signal=IndicatorSignal.NEUTRAL,
                interpretation="Insufficient candle history",
            )

        # 1. Calculate ATR
        true_ranges: list[float] = []
        for i in range(1, len(candles)):
            high = float(candles[i].high)
            low = float(candles[i].low)
            prev_close = float(candles[i - 1].close)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        atr_series = [sum(true_ranges[: self.period]) / self.period]
        for tr in true_ranges[self.period :]:
            atr_series.append((atr_series[-1] * (self.period - 1) + tr) / self.period)

        # 2. Compute Supertrend Bands
        current_candle = candles[-1]
        hl2 = (float(current_candle.high) + float(current_candle.low)) / 2.0
        current_atr = atr_series[-1]
        current_close = float(current_candle.close)

        upper_band = hl2 + (self.multiplier * current_atr)
        lower_band = hl2 - (self.multiplier * current_atr)

        is_bullish = current_close > lower_band

        if is_bullish:
            signal = IndicatorSignal.BUY
            trend_val = round(lower_band, 2)
            interpretation = (
                f"SuperTrend is GREEN (Support line at ${trend_val}). Bullish trend active."
            )
        else:
            signal = IndicatorSignal.SELL
            trend_val = round(upper_band, 2)
            interpretation = (
                f"SuperTrend is RED (Resistance line at ${trend_val}). Bearish trend active."
            )

        return IndicatorResult(
            name="supertrend",
            category=IndicatorCategory.TREND,
            values={"supertrend": trend_val, "atr": round(current_atr, 2)},
            signal=signal,
            interpretation=interpretation,
        )
