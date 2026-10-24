from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.application.services.agent_service import validate_market_data_freshness
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe


def _candle(timestamp: datetime) -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.H1,
        timestamp=timestamp,
        ohlcv=OHLCV(
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
        ),
    )


def test_freshness_accepts_recent_candle() -> None:
    now = datetime.now(tz=UTC)

    validate_market_data_freshness([_candle(now - timedelta(minutes=65))], Timeframe.H1, now)


def test_freshness_rejects_stale_candle() -> None:
    now = datetime.now(tz=UTC)

    with pytest.raises(ValueError, match="stale"):
        validate_market_data_freshness(
            [_candle(now - timedelta(hours=2, seconds=1))], Timeframe.H1, now
        )


def test_freshness_rejects_future_timestamp() -> None:
    now = datetime.now(tz=UTC)

    with pytest.raises(ValueError, match="future"):
        validate_market_data_freshness([_candle(now + timedelta(seconds=10))], Timeframe.H1, now)
