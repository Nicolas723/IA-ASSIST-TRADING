"""
Core Built-in Technical Indicators.
Includes RSI, MACD, EMA Suite, Bollinger Bands, and ATR.
"""

from __future__ import annotations

import math
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


def _get_close_prices(candles: list[Candle]) -> list[float]:
    return [float(c.close) for c in candles]


# ---------------------------------------------------------------------------
# 1. RSI (Relative Strength Index)
# ---------------------------------------------------------------------------


class RSIIndicator(BaseIndicator):
    metadata = IndicatorMetadata(
        name="rsi",
        display_name="Relative Strength Index",
        category=IndicatorCategory.MOMENTUM,
        description="Measures the speed and magnitude of recent price changes to evaluate overbought/oversold conditions.",
        parameters={"period": 14, "oversold": 30.0, "overbought": 70.0},
    )

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0) -> None:
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        prices = _get_close_prices(candles)
        if len(prices) < self.period + 1:
            return IndicatorResult(
                name="rsi",
                category=IndicatorCategory.MOMENTUM,
                values={"rsi": None},
                signal=IndicatorSignal.NEUTRAL,
                interpretation="Insufficient candle history",
            )

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]

        avg_gain = sum(gains[: self.period]) / self.period
        avg_loss = sum(losses[: self.period]) / self.period

        for i in range(self.period, len(deltas)):
            avg_gain = (avg_gain * (self.period - 1) + gains[i]) / self.period
            avg_loss = (avg_loss * (self.period - 1) + losses[i]) / self.period

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        rsi = round(rsi, 2)

        if rsi <= self.oversold:
            signal = IndicatorSignal.BUY
            interpretation = (
                f"Oversold condition ({rsi} <= {self.oversold}). Potential reversal upwards."
            )
        elif rsi >= self.overbought:
            signal = IndicatorSignal.SELL
            interpretation = (
                f"Overbought condition ({rsi} >= {self.overbought}). Potential pullback."
            )
        else:
            signal = IndicatorSignal.NEUTRAL
            interpretation = f"Neutral momentum ({rsi})."

        return IndicatorResult(
            name="rsi",
            category=IndicatorCategory.MOMENTUM,
            values={"rsi": rsi},
            signal=signal,
            interpretation=interpretation,
        )


# ---------------------------------------------------------------------------
# 2. MACD (Moving Average Convergence Divergence)
# ---------------------------------------------------------------------------


class MACDIndicator(BaseIndicator):
    metadata = IndicatorMetadata(
        name="macd",
        display_name="Moving Average Convergence Divergence",
        category=IndicatorCategory.TREND,
        description="Trend-following momentum indicator showing the relationship between two moving averages.",
        parameters={"fast_period": 12, "slow_period": 26, "signal_period": 9},
    )

    def __init__(
        self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
    ) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    def _calc_ema_series(self, prices: list[float], period: int) -> list[float]:
        multiplier = 2.0 / (period + 1.0)
        ema = [prices[0]]
        for p in prices[1:]:
            ema.append((p - ema[-1]) * multiplier + ema[-1])
        return ema

    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        prices = _get_close_prices(candles)
        if len(prices) < self.slow_period + self.signal_period:
            return IndicatorResult(
                name="macd",
                category=IndicatorCategory.TREND,
                values={"macd": None, "signal": None, "histogram": None},
                signal=IndicatorSignal.NEUTRAL,
                interpretation="Insufficient candle history",
            )

        fast_ema = self._calc_ema_series(prices, self.fast_period)
        slow_ema = self._calc_ema_series(prices, self.slow_period)

        macd_line = [f - s for f, s in zip(fast_ema, slow_ema, strict=False)]
        signal_line = self._calc_ema_series(macd_line, self.signal_period)
        histogram = [m - s for m, s in zip(macd_line, signal_line, strict=False)]

        curr_macd = round(macd_line[-1], 4)
        curr_sig = round(signal_line[-1], 4)
        curr_hist = round(histogram[-1], 4)
        prev_hist = round(histogram[-2], 4) if len(histogram) > 1 else 0.0

        if curr_hist > 0 and prev_hist <= 0:
            signal = IndicatorSignal.STRONG_BUY
            interpretation = "Bullish MACD crossover above signal line."
        elif curr_hist > 0:
            signal = IndicatorSignal.BUY
            interpretation = "Bullish momentum (histogram positive)."
        elif curr_hist < 0 and prev_hist >= 0:
            signal = IndicatorSignal.STRONG_SELL
            interpretation = "Bearish MACD crossover below signal line."
        elif curr_hist < 0:
            signal = IndicatorSignal.SELL
            interpretation = "Bearish momentum (histogram negative)."
        else:
            signal = IndicatorSignal.NEUTRAL
            interpretation = "MACD line convergent with signal line."

        return IndicatorResult(
            name="macd",
            category=IndicatorCategory.TREND,
            values={"macd": curr_macd, "signal": curr_sig, "histogram": curr_hist},
            signal=signal,
            interpretation=interpretation,
        )


# ---------------------------------------------------------------------------
# 3. EMA Suite (Exponential Moving Averages: 9, 21, 50, 200)
# ---------------------------------------------------------------------------


class EMASuiteIndicator(BaseIndicator):
    metadata = IndicatorMetadata(
        name="ema_suite",
        display_name="EMA Moving Average Suite",
        category=IndicatorCategory.TREND,
        description="Key Exponential Moving Averages (9, 21, 50, 200) for trend detection and dynamic support/resistance.",
        parameters={"periods": [9, 21, 50, 200]},
    )

    def __init__(self, periods: list[int] | None = None) -> None:
        self.periods = periods or [9, 21, 50, 200]

    def _calc_ema(self, prices: list[float], period: int) -> float | None:
        if len(prices) < period:
            return None
        multiplier = 2.0 / (period + 1.0)
        ema = sum(prices[:period]) / period  # start with SMA
        for p in prices[period:]:
            ema = (p - ema) * multiplier + ema
        return round(ema, 2)

    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        prices = _get_close_prices(candles)
        if not prices:
            return IndicatorResult(
                name="ema_suite",
                category=IndicatorCategory.TREND,
                values={},
                signal=IndicatorSignal.NEUTRAL,
                interpretation="No prices available",
            )

        current_price = prices[-1]
        values: dict[str, float | None] = {}
        for p in self.periods:
            values[f"ema_{p}"] = self._calc_ema(prices, p)

        ema_9 = values.get("ema_9")
        ema_21 = values.get("ema_21")
        ema_200 = values.get("ema_200")

        if ema_9 and ema_21 and current_price > ema_9 > ema_21:
            if ema_200 and current_price > ema_200:
                signal = IndicatorSignal.STRONG_BUY
                interpretation = (
                    "Strong bullish trend. Price above EMA 9, 21, and 200 (Golden alignment)."
                )
            else:
                signal = IndicatorSignal.BUY
                interpretation = "Short-term bullish trend (EMA 9 > EMA 21)."
        elif ema_9 and ema_21 and current_price < ema_9 < ema_21:
            if ema_200 and current_price < ema_200:
                signal = IndicatorSignal.STRONG_SELL
                interpretation = "Strong bearish trend. Price below EMA 9, 21, and 200."
            else:
                signal = IndicatorSignal.SELL
                interpretation = "Short-term bearish trend (EMA 9 < EMA 21)."
        else:
            signal = IndicatorSignal.NEUTRAL
            interpretation = "Mixed moving average signals (consolidation phase)."

        return IndicatorResult(
            name="ema_suite",
            category=IndicatorCategory.TREND,
            values=values,
            signal=signal,
            interpretation=interpretation,
        )


# ---------------------------------------------------------------------------
# 4. Bollinger Bands
# ---------------------------------------------------------------------------


class BollingerBandsIndicator(BaseIndicator):
    metadata = IndicatorMetadata(
        name="bollinger_bands",
        display_name="Bollinger Bands",
        category=IndicatorCategory.VOLATILITY,
        description="Volatility bands placed above and below a moving average.",
        parameters={"period": 20, "std_dev": 2.0},
    )

    def __init__(self, period: int = 20, std_dev: float = 2.0) -> None:
        self.period = period
        self.std_dev = std_dev

    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        prices = _get_close_prices(candles)
        if len(prices) < self.period:
            return IndicatorResult(
                name="bollinger_bands",
                category=IndicatorCategory.VOLATILITY,
                values={
                    "upper": None,
                    "middle": None,
                    "lower": None,
                    "bandwidth": None,
                    "percent_b": None,
                },
                signal=IndicatorSignal.NEUTRAL,
                interpretation="Insufficient candle history",
            )

        window = prices[-self.period :]
        middle = sum(window) / self.period
        variance = sum((x - middle) ** 2 for x in window) / self.period
        std = math.sqrt(variance)

        upper = middle + (self.std_dev * std)
        lower = middle - (self.std_dev * std)
        current_price = prices[-1]

        bandwidth = ((upper - lower) / middle) * 100 if middle else 0.0
        percent_b = ((current_price - lower) / (upper - lower)) if (upper - lower) else 0.5

        values: dict[str, float | None] = {
            "upper": round(upper, 2),
            "middle": round(middle, 2),
            "lower": round(lower, 2),
            "bandwidth": round(bandwidth, 2),
            "percent_b": round(percent_b, 4),
        }

        if current_price <= lower:
            signal = IndicatorSignal.BUY
            interpretation = f"Price (${current_price}) touching or below lower Bollinger Band (${values['lower']}). Oversold bounce zone."
        elif current_price >= upper:
            signal = IndicatorSignal.SELL
            interpretation = f"Price (${current_price}) touching or above upper Bollinger Band (${values['upper']}). Overbought resistance zone."
        else:
            signal = IndicatorSignal.NEUTRAL
            interpretation = f"Price inside normal volatility envelope (%B: {values['percent_b']})."

        return IndicatorResult(
            name="bollinger_bands",
            category=IndicatorCategory.VOLATILITY,
            values=values,
            signal=signal,
            interpretation=interpretation,
        )


# ---------------------------------------------------------------------------
# 5. ATR (Average True Range)
# ---------------------------------------------------------------------------


class ATRIndicator(BaseIndicator):
    metadata = IndicatorMetadata(
        name="atr",
        display_name="Average True Range",
        category=IndicatorCategory.VOLATILITY,
        description="Technical analysis volatility indicator that measures market volatility by decomposing the entire range of an asset price.",
        parameters={"period": 14},
    )

    def __init__(self, period: int = 14) -> None:
        self.period = period

    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        if len(candles) < self.period + 1:
            return IndicatorResult(
                name="atr",
                category=IndicatorCategory.VOLATILITY,
                values={"atr": None, "atr_percent": None},
                signal=IndicatorSignal.NEUTRAL,
                interpretation="Insufficient candle history",
            )

        true_ranges: list[float] = []
        for i in range(1, len(candles)):
            high = float(candles[i].high)
            low = float(candles[i].low)
            prev_close = float(candles[i - 1].close)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        atr = sum(true_ranges[: self.period]) / self.period
        for tr in true_ranges[self.period :]:
            atr = (atr * (self.period - 1) + tr) / self.period

        current_price = float(candles[-1].close)
        atr_pct = (atr / current_price) * 100 if current_price else 0.0

        atr = round(atr, 2)
        atr_pct = round(atr_pct, 2)

        interpretation = f"Average True Range is ${atr} ({atr_pct}% of price). Useful for dynamic stop-loss placement."

        return IndicatorResult(
            name="atr",
            category=IndicatorCategory.VOLATILITY,
            values={"atr": atr, "atr_percent": atr_pct},
            signal=IndicatorSignal.NEUTRAL,
            interpretation=interpretation,
        )
