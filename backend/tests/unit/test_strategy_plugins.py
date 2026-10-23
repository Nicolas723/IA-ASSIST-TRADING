"""
Unit tests for the bundled strategy plugins.

Covers EMA crossover, MACD momentum and RSI mean reversion: the "not enough
history" guard, the HOLD path, the BUY/SELL triggers, and the different shapes
of ``MarketContext`` accepted by ``generate_signal``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.strategy.entities import SignalSide
from src.plugins.strategies.ema_crossover import EMACrossoverStrategy
from src.plugins.strategies.macd_momentum import MACDMomentumStrategy
from src.plugins.strategies.rsi_mean_reversion import RSIMeanReversionStrategy


def _candles(closes: list[float]) -> list[Candle]:
    """Build a candle series from a list of closing prices (one hour apart)."""
    return [
        Candle(
            symbol="BTC/USDT",
            exchange=Exchange.BINANCE,
            timeframe=Timeframe.H1,
            timestamp=datetime.fromtimestamp(1700000000 + i * 3600, tz=UTC),
            ohlcv=OHLCV(
                open=Decimal(str(close)),
                high=Decimal(str(close + 1)),
                low=Decimal(str(close - 1)),
                close=Decimal(str(close)),
                volume=Decimal("10"),
            ),
            is_closed=True,
        )
        for i, close in enumerate(closes)
    ]


@pytest.mark.unit
def test_strategies_return_hold_without_enough_history():
    history = _candles([100.0] * 5)
    strategies = (
        EMACrossoverStrategy(),
        MACDMomentumStrategy(),
        RSIMeanReversionStrategy(),
    )
    for strategy in strategies:
        assert strategy.on_candle(history[-1], history).side == SignalSide.HOLD


@pytest.mark.unit
def test_ema_crossover_detects_bullish_cross():
    history = _candles([100.0] * 30 + [200.0])
    signal = EMACrossoverStrategy().on_candle(history[-1], history)

    assert signal.side == SignalSide.BUY
    assert signal.stop_loss is not None
    assert signal.take_profit is not None


@pytest.mark.unit
def test_ema_crossover_detects_bearish_cross():
    history = _candles([100.0] * 30 + [50.0])
    signal = EMACrossoverStrategy().on_candle(history[-1], history)

    assert signal.side == SignalSide.SELL


@pytest.mark.unit
def test_ema_crossover_holds_without_cross():
    history = _candles([100.0] * 40)
    assert EMACrossoverStrategy().on_candle(history[-1], history).side == SignalSide.HOLD


@pytest.mark.unit
def test_macd_momentum_detects_positive_flip():
    history = _candles([100.0] * 40 + [200.0])
    assert MACDMomentumStrategy().on_candle(history[-1], history).side == SignalSide.BUY


@pytest.mark.unit
def test_macd_momentum_detects_negative_flip():
    history = _candles([100.0] * 40 + [50.0])
    assert MACDMomentumStrategy().on_candle(history[-1], history).side == SignalSide.SELL


@pytest.mark.unit
def test_rsi_mean_reversion_sells_overbought():
    history = _candles([100.0 + i for i in range(40)])
    assert RSIMeanReversionStrategy().on_candle(history[-1], history).side == SignalSide.SELL


@pytest.mark.unit
def test_rsi_mean_reversion_buys_oversold():
    history = _candles([200.0 - i for i in range(40)])
    assert RSIMeanReversionStrategy().on_candle(history[-1], history).side == SignalSide.BUY


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy_cls",
    [EMACrossoverStrategy, MACDMomentumStrategy, RSIMeanReversionStrategy],
)
async def test_generate_signal_accepts_all_context_shapes(strategy_cls):
    strategy = strategy_cls()
    history = _candles([100.0] * 50)

    assert (await strategy.generate_signal(None)).reason == "No candles in context"
    assert (
        await strategy.generate_signal(SimpleNamespace(candles=[]))
    ).reason == "No candles in context"
    assert (
        await strategy.generate_signal(SimpleNamespace(candles="invalid"))
    ).reason == "No candles in context"

    by_list = await strategy.generate_signal(SimpleNamespace(candles=history))
    by_dict = await strategy.generate_signal(SimpleNamespace(candles={"1h": history}))
    assert by_list.side == by_dict.side
