"""
Read-only market-regime detector.

Classifies the most recent *closed* candles into a descriptive trend and
volatility regime using deterministic rules (EMA separation/slope and ATR
percentile). It exists to give operators context and to label measurements:
it never changes signals, position sizing, risk vetoes or execution.

A regime label is a description of past price behaviour, not a forecast and
not a probability. Small samples, gaps and regime transitions can make any
classification wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.application.services.market_data_service import MarketDataService
    from src.domain.market.entities import Candle, Timeframe

RegimeStatus = Literal["insufficient_data", "classified"]
TrendRegime = Literal["uptrend", "downtrend", "range"]
VolatilityRegime = Literal["low", "normal", "high"]

DISCLAIMER = (
    "Descriptive classification of past closed candles. It is not a forecast, "
    "a probability or a trading signal; it does not authorize or block orders."
)


class RegimeThresholds(BaseModel):
    """Rules applied to classify the regime, exposed for reproducibility."""

    ema_fast_period: int
    ema_slow_period: int
    atr_period: int
    slope_lookback: int
    trend_separation_pct: float
    slope_threshold_pct: float
    high_volatility_percentile: float
    low_volatility_percentile: float


class RegimeMetrics(BaseModel):
    """Measurements behind the label. All values are descriptive."""

    sample_size: int = 0
    active_candles_excluded: int = 0
    last_close: float | None = None
    last_candle_at: datetime | None = None
    data_age_seconds: float | None = None
    data_stale: bool | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_separation_pct: float | None = None
    ema_slope_pct: float | None = None
    atr_pct: float | None = None
    atr_percentile: float | None = None
    trend_strength: float | None = None  # heuristic 0-100 (0 for range), not a probability
    lookback_high: float | None = None
    lookback_low: float | None = None
    range_position_pct: float | None = None


class MarketRegimeReport(BaseModel):
    """Regime label plus the evidence used to produce it."""

    generated_at: datetime
    symbol: str
    timeframe: str
    status: RegimeStatus
    trend: TrendRegime | None = None
    volatility: VolatilityRegime | None = None
    metrics: RegimeMetrics = Field(default_factory=RegimeMetrics)
    thresholds: RegimeThresholds
    reasons: list[str] = Field(default_factory=list)
    descriptive_only: bool = True
    affects_trading: bool = False
    execution_enabled: bool = False
    disclaimer: str = DISCLAIMER


def classify_regime(
    candles: Sequence[Candle],
    *,
    symbol: str,
    timeframe: str,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
    ema_fast_period: int = 20,
    ema_slow_period: int = 50,
    atr_period: int = 14,
    volatility_window: int = 100,
    slope_lookback: int = 10,
    trend_separation_pct: float = 0.25,
    slope_threshold_pct: float = 0.10,
    high_volatility_percentile: float = 80.0,
    low_volatility_percentile: float = 20.0,
) -> MarketRegimeReport:
    """Classify a candle series without side effects.

    Only closed candles are used; unclosed candles are counted and excluded so
    an in-progress candle cannot flip the label. The function never raises on
    short series: it returns ``status='insufficient_data'`` instead.
    """
    if ema_fast_period < 2 or ema_slow_period <= ema_fast_period:
        raise ValueError("EMA periods must satisfy 2 <= fast < slow.")
    if atr_period < 1 or volatility_window < 2 or slope_lookback < 1:
        raise ValueError("ATR period, volatility window and slope lookback must be positive.")
    if not 0.0 <= low_volatility_percentile < high_volatility_percentile <= 100.0:
        raise ValueError("Volatility percentiles must satisfy 0 <= low < high <= 100.")

    reference = now or datetime.now(tz=UTC)
    thresholds = RegimeThresholds(
        ema_fast_period=ema_fast_period,
        ema_slow_period=ema_slow_period,
        atr_period=atr_period,
        slope_lookback=slope_lookback,
        trend_separation_pct=trend_separation_pct,
        slope_threshold_pct=slope_threshold_pct,
        high_volatility_percentile=high_volatility_percentile,
        low_volatility_percentile=low_volatility_percentile,
    )

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    closed = [candle for candle in ordered if candle.is_closed]
    excluded = len(ordered) - len(closed)
    metrics = RegimeMetrics(sample_size=len(closed), active_candles_excluded=excluded)

    min_required = max(
        ema_slow_period + slope_lookback,
        atr_period + 2,
        volatility_window // 2,
    )
    if len(closed) < min_required:
        return MarketRegimeReport(
            generated_at=reference,
            symbol=symbol,
            timeframe=timeframe,
            status="insufficient_data",
            metrics=metrics,
            thresholds=thresholds,
            reasons=[
                f"Only {len(closed)} closed candles available; at least "
                f"{min_required} are required for a stable label.",
            ],
        )

    closes = [float(candle.close) for candle in closed]
    highs = [float(candle.high) for candle in closed]
    lows = [float(candle.low) for candle in closed]
    last_close = closes[-1]

    fast_series = _ema(closes, ema_fast_period)
    slow_series = _ema(closes, ema_slow_period)
    ema_fast = fast_series[-1]
    ema_slow = slow_series[-1]
    separation_pct = (ema_fast - ema_slow) / ema_slow * 100.0

    slope_base_index = max(0, len(fast_series) - 1 - slope_lookback)
    slope_base = fast_series[slope_base_index]
    slope_pct = (ema_fast - slope_base) / slope_base * 100.0 if slope_base else 0.0

    atr_series = _atr_series(highs, lows, closes, atr_period)
    atr_pct = atr_series[-1] / last_close * 100.0 if last_close else None
    atr_window = atr_series[-volatility_window:]
    atr_percentile = _percentile_rank(atr_window, atr_series[-1])

    if separation_pct >= trend_separation_pct and slope_pct >= slope_threshold_pct:
        trend: TrendRegime = "uptrend"
    elif separation_pct <= -trend_separation_pct and slope_pct <= -slope_threshold_pct:
        trend = "downtrend"
    else:
        trend = "range"

    volatility: VolatilityRegime | None = None
    if atr_percentile is not None:
        if atr_percentile >= high_volatility_percentile:
            volatility = "high"
        elif atr_percentile <= low_volatility_percentile:
            volatility = "low"
        else:
            volatility = "normal"

    strength = 0.0
    if trend != "range":
        separation_multiple = abs(separation_pct) / trend_separation_pct
        slope_multiple = abs(slope_pct) / slope_threshold_pct
        strength = round(
            100.0
            * (0.6 * min(separation_multiple / 5.0, 1.0) + 0.4 * min(slope_multiple / 5.0, 1.0)),
            2,
        )

    lookback = min(len(closed), volatility_window)
    lookback_high = max(highs[-lookback:])
    lookback_low = min(lows[-lookback:])
    range_position = (
        (last_close - lookback_low) / (lookback_high - lookback_low) * 100.0
        if lookback_high > lookback_low
        else None
    )

    last_candle_at = closed[-1].timestamp
    if last_candle_at.tzinfo is None:
        last_candle_at = last_candle_at.replace(tzinfo=UTC)
    age_seconds = max(0.0, (reference - last_candle_at).total_seconds())
    stale = age_seconds > stale_after_seconds if stale_after_seconds is not None else None

    metrics = RegimeMetrics(
        sample_size=len(closed),
        active_candles_excluded=excluded,
        last_close=round(last_close, 8),
        last_candle_at=last_candle_at,
        data_age_seconds=round(age_seconds, 1),
        data_stale=stale,
        ema_fast=round(ema_fast, 8),
        ema_slow=round(ema_slow, 8),
        ema_separation_pct=round(separation_pct, 4),
        ema_slope_pct=round(slope_pct, 4),
        atr_pct=round(atr_pct, 4) if atr_pct is not None else None,
        atr_percentile=atr_percentile,
        trend_strength=strength,
        lookback_high=round(lookback_high, 8),
        lookback_low=round(lookback_low, 8),
        range_position_pct=round(range_position, 2) if range_position is not None else None,
    )

    reasons = [
        f"EMA{ema_fast_period} is {separation_pct:+.2f}% vs EMA{ema_slow_period} and its "
        f"{slope_lookback}-bar slope is {slope_pct:+.2f}%.",
    ]
    if atr_percentile is not None:
        reasons.append(
            f"ATR{atr_period} is {atr_pct:.2f}% of price at percentile "
            f"{atr_percentile:.1f} of the last {len(atr_window)} ATR values."
        )
    if trend == "range":
        reasons.append("EMA separation or slope is below the trend threshold; classified as range.")
    if excluded:
        reasons.append(f"{excluded} unclosed candle(s) were excluded.")
    if stale:
        reasons.append(
            f"Last closed candle is {age_seconds:.0f}s old; the label may describe stale data."
        )

    return MarketRegimeReport(
        generated_at=reference,
        symbol=symbol,
        timeframe=timeframe,
        status="classified",
        trend=trend,
        volatility=volatility,
        metrics=metrics,
        thresholds=thresholds,
        reasons=reasons,
    )


class RegimeService:
    """Fetch candles and classify the current market regime (read-only)."""

    def __init__(self, market_service: MarketDataService) -> None:
        self._market = market_service

    async def analyze(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        now: datetime | None = None,
    ) -> MarketRegimeReport:
        candles = await self._market.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
        return classify_regime(
            candles,
            symbol=symbol,
            timeframe=timeframe.value,
            now=now,
            stale_after_seconds=int(timeframe.seconds * 2.5),
        )


def _ema(values: Sequence[float], period: int) -> list[float]:
    """EMA seeded with the SMA of the first ``period`` values."""
    if len(values) < period:
        return []
    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    result = [current]
    for value in values[period:]:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def _atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> list[float]:
    """Wilder ATR series built from true ranges."""
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(true_ranges) < period:
        return []
    current = sum(true_ranges[:period]) / period
    series = [current]
    for true_range in true_ranges[period:]:
        current = ((period - 1) * current + true_range) / period
        series.append(current)
    return series


def _percentile_rank(values: Sequence[float], target: float) -> float | None:
    """Share of observations strictly below ``target``, scaled to 0-100."""
    if len(values) < 2:
        return None
    below = sum(1 for value in values if value < target)
    return round(below / (len(values) - 1) * 100.0, 2)
