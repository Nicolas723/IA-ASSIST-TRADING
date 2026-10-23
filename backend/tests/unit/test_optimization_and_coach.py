"""
Unit tests for Parameter Optimizer & Performance Coach.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.optimization.entities import (
    OptimizationConfig,
    OptimizationTarget,
    ParameterRange,
)
from src.domain.optimization.parameter_optimizer import ParameterOptimizer
from src.domain.optimization.performance_coach import PerformanceCoach
from src.domain.strategy.entities import BacktestTrade


def create_candles(count: int) -> list[Candle]:
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    price = 100.0
    for i in range(count):
        price += 1.0 if i % 2 == 0 else -0.5
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                ohlcv=OHLCV(
                    open=Decimal(str(round(price - 0.2, 2))),
                    high=Decimal(str(round(price + 1.0, 2))),
                    low=Decimal(str(round(price - 1.0, 2))),
                    close=Decimal(str(round(price, 2))),
                    volume=Decimal("500.0"),
                ),
            )
        )
    return candles


def test_parameter_optimizer_insufficient_candles():
    optimizer = ParameterOptimizer()
    config = OptimizationConfig(strategy_name="rsi_mean_reversion")
    candles = create_candles(15)  # <30

    with pytest.raises(ValueError, match="Insufficient candles"):
        optimizer.optimize(config, candles)


def test_parameter_optimizer_unknown_strategy():
    optimizer = ParameterOptimizer()
    config = OptimizationConfig(strategy_name="non_existent_strategy_xyz")
    candles = create_candles(40)

    with pytest.raises(ValueError, match="not found"):
        optimizer.optimize(config, candles)


def test_parameter_optimizer_successful_grid_search():
    optimizer = ParameterOptimizer()
    config = OptimizationConfig(
        strategy_name="rsi_mean_reversion",
        symbol="BTC/USDT",
        timeframe="1h",
        target_metric=OptimizationTarget.NET_RETURN,
        parameter_ranges=[
            ParameterRange(name="rsi_period", min_value=10, max_value=14, step=4, is_integer=True),
        ],
    )
    candles = create_candles(40)

    result = optimizer.optimize(config, candles)

    assert result.strategy_name == "rsi_mean_reversion"
    assert result.combinations_evaluated > 0
    assert len(result.top_parameter_sets) > 0
    assert result.recommendation_summary != ""


def test_performance_coach_empty_trades():
    report = PerformanceCoach.analyze_trades([])
    assert report.trades_analyzed_count == 0
    assert report.win_rate_pct == 0.0
    assert len(report.lessons) == 0
    assert "No completed trades" in report.ai_coach_critique


def test_performance_coach_with_mixed_trades():
    trades = [
        BacktestTrade(
            id=1,
            symbol="BTC/USDT",
            side="BUY",
            entry_time="2026-01-01T00:00:00",
            exit_time="2026-01-01T02:00:00",
            entry_price=100.0,
            exit_price=110.0,
            quantity=1.0,
            pnl=10.0,
            pnl_pct=10.0,
            fee_paid=0.1,
            holding_candles=2,
            exit_reason="Take Profit Hit",
        ),
        BacktestTrade(
            id=2,
            symbol="BTC/USDT",
            side="BUY",
            entry_time="2026-01-01T03:00:00",
            exit_time="2026-01-01T05:00:00",
            entry_price=110.0,
            exit_price=105.0,
            quantity=1.0,
            pnl=-5.0,
            pnl_pct=-4.5,
            fee_paid=0.1,
            holding_candles=2,
            exit_reason="Stop Loss Hit",
        ),
    ]

    report = PerformanceCoach.analyze_trades(trades)

    assert report.trades_analyzed_count == 2
    assert report.win_rate_pct == 50.0
    assert report.net_profit == 5.0
    assert len(report.lessons) == 2
    assert "Profitable Asymmetry" in report.ai_coach_critique
