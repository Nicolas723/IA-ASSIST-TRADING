"""Read-only regime classification: deterministic, closed-candle-only and non-trading."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.application.services.regime_service import RegimeService, classify_regime
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.interfaces.api.v1.deps import get_market_data_service

START = datetime(2026, 1, 1, tzinfo=UTC)


def _candles(
    closes: list[float],
    *,
    half_ranges: list[float] | None = None,
    is_closed: bool = True,
    start: datetime = START,
) -> list[Candle]:
    candles: list[Candle] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        half_range = half_ranges[index] if half_ranges else close * 0.0005
        high = max(previous, close) + half_range
        low = min(previous, close) - half_range
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=start + timedelta(hours=index),
                ohlcv=OHLCV(
                    open=Decimal(str(previous)),
                    high=Decimal(str(high)),
                    low=Decimal(str(low)),
                    close=Decimal(str(close)),
                    volume=Decimal("10"),
                ),
                is_closed=is_closed,
            )
        )
        previous = close
    return candles


def _report(candles: list[Candle], **kwargs: object):
    return classify_regime(
        candles,
        symbol="BTC/USDT",
        timeframe="1h",
        now=START + timedelta(hours=len(candles) + 1),
        **kwargs,  # type: ignore[arg-type]
    )


def test_uptrend_is_classified_without_enabling_execution() -> None:
    closes = [100.0 * (1.005**index) for index in range(120)]

    report = _report(_candles(closes))

    assert report.status == "classified"
    assert report.trend == "uptrend"
    assert report.metrics.ema_separation_pct is not None
    assert report.metrics.ema_separation_pct > 0
    assert report.metrics.trend_strength is not None
    assert report.metrics.trend_strength > 0
    assert report.descriptive_only is True
    assert report.affects_trading is False
    assert report.execution_enabled is False


def test_downtrend_is_classified() -> None:
    closes = [100.0 * (0.995**index) for index in range(120)]

    report = _report(_candles(closes))

    assert report.trend == "downtrend"
    assert report.metrics.ema_separation_pct is not None
    assert report.metrics.ema_separation_pct < 0


def test_sideways_market_is_range() -> None:
    closes = [100.0 + 2.0 * math.sin(index / 6.0) for index in range(120)]

    report = _report(_candles(closes))

    assert report.status == "classified"
    assert report.trend == "range"
    assert report.metrics.trend_strength == 0.0


def test_short_series_returns_insufficient_data() -> None:
    report = _report(_candles([100.0 + index for index in range(10)]))

    assert report.status == "insufficient_data"
    assert report.trend is None
    assert report.volatility is None
    assert report.metrics.sample_size == 10
    assert report.execution_enabled is False
    assert "required" in report.reasons[0]


def test_unclosed_candle_is_excluded_from_the_label() -> None:
    closes = [100.0 * (1.005**index) for index in range(120)]
    candles = _candles(closes)
    candles.append(
        _candles([1.0], start=START + timedelta(hours=120))[0].model_copy(
            update={"is_closed": False}
        )
    )

    report = _report(candles)

    assert report.trend == "uptrend"
    assert report.metrics.sample_size == 120
    assert report.metrics.active_candles_excluded == 1
    assert any("unclosed" in reason for reason in report.reasons)


def test_volatility_spike_is_high() -> None:
    closes = [100.0] * 120
    half_ranges = [0.1] * 115 + [5.0] * 5

    report = _report(_candles(closes, half_ranges=half_ranges))

    assert report.volatility == "high"
    assert report.metrics.atr_percentile is not None
    assert report.metrics.atr_percentile >= 80


def test_flat_volatility_is_low() -> None:
    closes = [100.0] * 120

    report = _report(_candles(closes, half_ranges=[0.1] * 120))

    assert report.volatility == "low"
    assert report.metrics.atr_percentile is not None
    assert report.metrics.atr_percentile <= 20


def test_stale_data_is_flagged() -> None:
    closes = [100.0 * (1.005**index) for index in range(120)]
    candles = _candles(closes)

    report = classify_regime(
        candles,
        symbol="BTC/USDT",
        timeframe="1h",
        now=START + timedelta(days=30),
        stale_after_seconds=Timeframe.H1.seconds * 2.5,
    )

    assert report.metrics.data_stale is True
    assert report.metrics.data_age_seconds is not None
    assert any("stale" in reason for reason in report.reasons)


def test_invalid_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError, match="EMA periods"):
        classify_regime(
            [],
            symbol="BTC/USDT",
            timeframe="1h",
            ema_fast_period=50,
            ema_slow_period=20,
        )


class _FakeMarketService:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls: list[tuple[str, Timeframe, int]] = []

    async def get_candles(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit))
        return self.candles


@pytest.mark.asyncio
async def test_service_fetches_candles_and_flags_staleness() -> None:
    closes = [100.0 * (1.005**index) for index in range(120)]
    market = _FakeMarketService(_candles(closes))

    report = await RegimeService(market).analyze(  # type: ignore[arg-type]
        symbol="BTC/USDT",
        timeframe=Timeframe.H1,
        limit=150,
    )

    assert market.calls == [("BTC/USDT", Timeframe.H1, 150)]
    assert report.metrics.data_stale is True
    assert report.execution_enabled is False


@pytest.mark.asyncio
async def test_regime_endpoint_is_read_only(async_client, fastapi_app) -> None:
    closes = [100.0 * (1.005**index) for index in range(120)]
    market = _FakeMarketService(_candles(closes))
    fastapi_app.dependency_overrides[get_market_data_service] = lambda: market
    try:
        response = await async_client.get(
            "/api/v1/market/BTC/USDT/regime",
            params={"timeframe": "1h", "limit": 200},
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_market_data_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["trend"] == "uptrend"
    assert body["descriptive_only"] is True
    assert body["affects_trading"] is False
    assert body["execution_enabled"] is False
