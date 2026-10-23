"""
Unit tests for Indicators and Multi-Agent Committee components.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.domain.agents.entities import (
    AgentOutput,
    AgentType,
    PriceTarget,
    SignalDirection,
)
from src.domain.indicators.builtin import (
    BollingerBandsIndicator,
    EMASuiteIndicator,
    MACDIndicator,
    RSIIndicator,
)
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe


def _generate_synthetic_candles(count: int = 100, trend: float = 100.0) -> list[Candle]:
    candles = []
    base_price = 50000.0
    for i in range(count):
        price = base_price + (i * trend)
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=datetime.fromtimestamp(1700000000 + (i * 3600), tz=UTC),
                ohlcv=OHLCV(
                    open=Decimal(str(round(price - 10, 2))),
                    high=Decimal(str(round(price + 50, 2))),
                    low=Decimal(str(round(price - 50, 2))),
                    close=Decimal(str(round(price, 2))),
                    volume=Decimal(str(round(100.0 + (i * 2), 2))),
                ),
                is_closed=True,
            )
        )
    return candles


@pytest.mark.unit
def test_rsi_indicator():
    rsi = RSIIndicator(period=14)
    candles = _generate_synthetic_candles(count=50, trend=100.0)
    result = rsi.calculate(candles)
    assert result.name == "rsi"
    assert "rsi" in result.values
    assert result.values["rsi"] is not None
    assert result.values["rsi"] > 50.0


@pytest.mark.unit
def test_macd_indicator():
    macd = MACDIndicator(fast_period=12, slow_period=26, signal_period=9)
    candles = _generate_synthetic_candles(count=60, trend=100.0)
    result = macd.calculate(candles)
    assert result.name == "macd"
    assert "macd" in result.values
    assert "signal" in result.values
    assert "histogram" in result.values


@pytest.mark.unit
def test_ema_suite_indicator():
    ema = EMASuiteIndicator()
    candles = _generate_synthetic_candles(count=210, trend=10.0)
    result = ema.calculate(candles)
    assert result.name == "ema_suite"
    assert "ema_9" in result.values
    assert "ema_21" in result.values
    assert "ema_50" in result.values
    assert "ema_200" in result.values
    assert result.values["ema_9"] is not None


@pytest.mark.unit
def test_bollinger_bands_indicator():
    bb = BollingerBandsIndicator(period=20, std_dev=2.0)
    candles = _generate_synthetic_candles(count=50, trend=20.0)
    result = bb.calculate(candles)
    assert result.name == "bollinger_bands"
    assert "upper" in result.values
    assert "middle" in result.values
    assert "lower" in result.values
    assert result.values["upper"] > result.values["lower"]


@pytest.mark.unit
def test_specialist_agent_output():
    output = AgentOutput(
        task_id=uuid4(),
        agent_type=AgentType.TECHNICAL_ANALYST,
        symbol="BTC/USDT",
        signal=SignalDirection.BUY,
        confidence=0.85,
        reasoning="Bullish MACD crossover confirmed with RSI above 50 and price moving above the 200 Exponential Moving Average.",
        price_targets=PriceTarget(
            entry=65000.0,
            stop_loss=63500.0,
            take_profit_1=68000.0,
        ),
    )
    assert output.signal == SignalDirection.BUY
    assert output.confidence == 0.85
    assert output.price_targets is not None
    assert output.price_targets.entry == 65000.0
