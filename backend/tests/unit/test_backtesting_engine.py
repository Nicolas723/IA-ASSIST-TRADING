"""
Unit tests for Institutional Quantitative Backtesting Engine.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.application.services.backtesting_engine import BacktestingEngine
from src.domain.market.entities import OHLCV, Candle, Exchange, MarketContext, Timeframe
from src.domain.strategy.base import BaseStrategy
from src.domain.strategy.entities import (
    BacktestConfig,
    Signal,
    SignalSide,
    StrategyMetadata,
)


class MockStrategy(BaseStrategy):
    """Simple strategy mock for backtesting tests."""

    metadata = StrategyMetadata(
        name="mock_strategy",
        display_name="Mock Strategy",
        description="Mock for unit tests",
        default_parameters={},
    )

    def __init__(
        self,
        buy_indices: set[int] | None = None,
        sell_indices: set[int] | None = None,
        sl_pct: float | None = None,
        tp_pct: float | None = None,
        parameters: dict[str, Any] | None = None,
    ):
        super().__init__(parameters)
        self.buy_indices = buy_indices or set()
        self.sell_indices = sell_indices or set()
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.candle_counter = 0

    async def generate_signal(self, context: MarketContext | Any) -> Signal:
        return Signal(side=SignalSide.HOLD, confidence=0.0)

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal:
        idx = self.candle_counter
        self.candle_counter += 1

        close = float(candle.close)
        sl = close * (1.0 - self.sl_pct) if self.sl_pct else None
        tp = close * (1.0 + self.tp_pct) if self.tp_pct else None

        if idx in self.buy_indices:
            return Signal(
                side=SignalSide.BUY, confidence=0.9, stop_loss=sl, take_profit=tp, reason="Mock Buy"
            )
        elif idx in self.sell_indices:
            return Signal(side=SignalSide.SELL, confidence=0.8, reason="Mock Sell")

        return Signal(side=SignalSide.HOLD, confidence=0.0)


def create_dummy_candles(
    count: int, start_price: float = 100.0, trend: float = 1.0
) -> list[Candle]:
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    candles = []
    price = start_price
    for i in range(count):
        price += trend
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                ohlcv=OHLCV(
                    open=Decimal(str(round(price - 0.5, 2))),
                    high=Decimal(str(round(price + 2.0, 2))),
                    low=Decimal(str(round(price - 2.0, 2))),
                    close=Decimal(str(round(price, 2))),
                    volume=Decimal("1000.0"),
                ),
            )
        )
    return candles


def test_backtest_insufficient_candles():
    engine = BacktestingEngine()
    candles = create_dummy_candles(5)
    config = BacktestConfig(strategy_name="mock", symbol="BTC/USDT", timeframe="1h")
    strategy = MockStrategy()

    with pytest.raises(ValueError, match="Insufficient candles"):
        engine.run(strategy, candles, config)


def test_backtest_no_trades():
    engine = BacktestingEngine()
    candles = create_dummy_candles(15)
    config = BacktestConfig(
        strategy_name="mock", symbol="BTC/USDT", timeframe="1h", initial_capital=10000.0
    )
    strategy = MockStrategy()  # Always neutral

    result = engine.run(strategy, candles, config)

    assert result.total_trades == 0
    assert result.net_profit == 0.0
    assert result.total_return_pct == 0.0
    assert result.win_rate_pct == 0.0
    assert result.profit_factor == 0.0
    assert len(result.equity_curve) == 15


def test_backtest_buy_and_sell_cycle():
    engine = BacktestingEngine()
    candles = create_dummy_candles(20, start_price=100.0, trend=1.0)
    config = BacktestConfig(
        strategy_name="mock",
        symbol="BTC/USDT",
        timeframe="1h",
        initial_capital=10000.0,
        fee_rate=0.001,
        slippage=0.0005,
    )

    # Buy at index 2, sell at index 8
    strategy = MockStrategy(buy_indices={2}, sell_indices={8})

    result = engine.run(strategy, candles, config)

    assert result.total_trades == 1
    assert result.winning_trades == 1
    assert result.win_rate_pct == 100.0
    assert result.profit_factor == 99.0
    assert result.net_profit > 0


def test_backtest_stop_loss_trigger():
    engine = BacktestingEngine()

    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    candles = []
    prices = [100, 102, 104, 105, 90, 88, 85, 84, 82, 80, 78, 76]  # Drops at idx 4
    for i, p in enumerate(prices):
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                ohlcv=OHLCV(
                    open=Decimal(str(p)),
                    high=Decimal(str(p + 1)),
                    low=Decimal(str(p - 1)),
                    close=Decimal(str(p)),
                    volume=Decimal("100"),
                ),
            )
        )

    config = BacktestConfig(
        strategy_name="mock", symbol="BTC/USDT", timeframe="1h", initial_capital=10000.0
    )
    # Buy at idx 1 with 5% Stop Loss (SL = 102 * 0.95 = 96.9)
    strategy = MockStrategy(buy_indices={1}, sl_pct=0.05)

    result = engine.run(strategy, candles, config)

    assert result.total_trades == 1
    assert result.losing_trades == 1
    assert result.trades[0].exit_reason == "Stop Loss Hit"


def test_backtest_take_profit_trigger():
    engine = BacktestingEngine()

    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    candles = []
    prices = [100, 102, 103, 120, 122, 125, 126, 127, 128, 129, 130, 131]  # Jumps at idx 3
    for i, p in enumerate(prices):
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                ohlcv=OHLCV(
                    open=Decimal(str(p)),
                    high=Decimal(str(p + 2)),
                    low=Decimal(str(p - 1)),
                    close=Decimal(str(p)),
                    volume=Decimal("100"),
                ),
            )
        )

    config = BacktestConfig(
        strategy_name="mock", symbol="BTC/USDT", timeframe="1h", initial_capital=10000.0
    )
    # Buy at idx 1 with 10% Take Profit (TP = 102 * 1.10 = 112.2)
    strategy = MockStrategy(buy_indices={1}, tp_pct=0.10)

    result = engine.run(strategy, candles, config)

    assert result.total_trades == 1
    assert result.winning_trades == 1
    assert result.trades[0].exit_reason == "Take Profit Hit"


def test_backtest_lingering_position_close():
    engine = BacktestingEngine()
    candles = create_dummy_candles(15)
    config = BacktestConfig(
        strategy_name="mock", symbol="BTC/USDT", timeframe="1h", initial_capital=10000.0
    )

    # Buy at idx 10 and never explicit sell
    strategy = MockStrategy(buy_indices={10})

    result = engine.run(strategy, candles, config)

    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "End of Backtest Window"
