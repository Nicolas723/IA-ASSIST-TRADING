"""Deterministic multi-market pre-scan: ranking, filters and guard rails."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.market_prescan_service import (
    MarketPreScanService,
    _regime_adjustment,
)
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.shared.config.settings import MAX_SCAN_SYMBOLS

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _candles(closes: list[float], symbol: str) -> list[Candle]:
    start = NOW - timedelta(hours=len(closes))
    return [
        Candle(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=Timeframe.H1,
            timestamp=start + timedelta(hours=index),
            ohlcv=OHLCV(
                open=Decimal(str(close)),
                high=Decimal(str(close)),
                low=Decimal(str(close)),
                close=Decimal(str(close)),
                volume=Decimal("1"),
            ),
            is_closed=True,
        )
        for index, close in enumerate(closes)
    ]


def _trend(total_return_pct: float, points: int = 25) -> list[float]:
    step = total_return_pct / (points - 1)
    closes = [100.0]
    for _ in range(points - 1):
        closes.append(closes[-1] * (1.0 + step / 100.0))
    return closes


def _market(candles_by_symbol: dict[str, list[Candle]]) -> MagicMock:
    market = MagicMock()

    async def get_candles(symbol: str, **_kwargs: object) -> list[Candle]:
        return candles_by_symbol[symbol]

    market.get_candles = AsyncMock(side_effect=get_candles)
    return market


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prescan_ranks_the_strongest_recent_market_first() -> None:
    market = _market(
        {
            "BTC/USDT": _candles(_trend(1.0), "BTC/USDT"),
            "ETH/USDT": _candles(_trend(8.0), "ETH/USDT"),
            "SOL/USDT": _candles(_trend(-6.0), "SOL/USDT"),
        }
    )

    result = await MarketPreScanService(market).scan(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"], timeframe="1h", lookback_candles=24, now=NOW
    )

    assert [candidate.symbol for candidate in result.ranked] == [
        "ETH/USDT",
        "BTC/USDT",
        "SOL/USDT",
    ]
    assert [candidate.rank for candidate in result.ranked] == [1, 2, 3]
    assert result.execution_enabled is False
    assert "not an expected return" in result.selection_basis
    assert result.ranked[0].selection_score is not None
    assert result.ranked[0].selection_score > result.ranked[1].selection_score


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prescan_marks_failed_markets_unavailable_without_aborting() -> None:
    market = MagicMock()

    async def get_candles(symbol: str, **_kwargs: object) -> list[Candle]:
        if symbol == "ETH/USDT":
            raise RuntimeError("exchange unavailable")
        return _candles(_trend(2.0), symbol)

    market.get_candles = AsyncMock(side_effect=get_candles)

    result = await MarketPreScanService(market).scan(
        ["BTC/USDT", "ETH/USDT"], timeframe="1h", lookback_candles=24, now=NOW
    )

    by_symbol = {candidate.symbol: candidate for candidate in result.candidates}
    assert by_symbol["BTC/USDT"].status == "observed"
    assert by_symbol["ETH/USDT"].status == "unavailable"
    assert [candidate.symbol for candidate in result.ranked] == ["BTC/USDT"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prescan_excludes_the_still_open_candle() -> None:
    candles = _candles(_trend(5.0, points=25), "BTC/USDT")
    # Append a candle whose close time is in the future; it must not affect the
    # ranking because the daemon would be looking at an unfinished bar.
    candles.append(
        candles[-1].model_copy(
            update={
                "timestamp": NOW,
                "ohlcv": candles[-1].ohlcv.model_copy(update={"close": Decimal("1000")}),
            }
        )
    )
    market = _market({"BTC/USDT": candles})

    result = await MarketPreScanService(market).scan(
        ["BTC/USDT"], timeframe="1h", lookback_candles=24, now=NOW
    )

    candidate = result.candidates[0]
    assert candidate.status == "observed"
    assert candidate.last_price != 1000.0
    assert candidate.lookback_return_pct is not None
    assert candidate.lookback_return_pct < 100.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prescan_excludes_stale_markets_from_selection() -> None:
    candles = [
        candle.model_copy(update={"timestamp": candle.timestamp - timedelta(hours=10)})
        for candle in _candles(_trend(5.0, points=25), "BTC/USDT")
    ]
    market = _market({"BTC/USDT": candles})

    result = await MarketPreScanService(market).scan(
        ["BTC/USDT"], timeframe="1h", lookback_candles=24, now=NOW
    )

    assert result.candidates[0].status == "stale_data"
    assert result.ranked == []


@pytest.mark.unit
def test_regime_adjustment_is_small_bounded_and_directional() -> None:
    assert _regime_adjustment("uptrend", 100.0) == pytest.approx(0.5)
    assert _regime_adjustment("uptrend", 50.0) == pytest.approx(0.25)
    assert _regime_adjustment("downtrend", 100.0) == pytest.approx(-0.5)
    assert _regime_adjustment("range", 100.0) == 0.0
    assert _regime_adjustment(None, None) == 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prescan_rejects_invalid_universes() -> None:
    service = MarketPreScanService(MagicMock())

    # Same cap as the markets the settings allow, so a full list always scans.
    with pytest.raises(ValueError, match=f"between 1 and {MAX_SCAN_SYMBOLS}"):
        await service.scan([f"SYM{index}/USDT" for index in range(MAX_SCAN_SYMBOLS + 1)])

    with pytest.raises(ValueError, match="BASE/QUOTE"):
        await service.scan(["BTCUSDT"])
