"""
Unit tests for AutonomousEngine and Autonomous Bot lifecycle.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.autonomous_engine import (
    AutonomousBotConfig,
    AutonomousEngine,
)
from src.domain.agents.entities import CoordinatorDecision, SignalDirection
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_engine_lifecycle():
    engine = AutonomousEngine()
    assert not engine.is_running
    assert engine.total_cycles == 0

    config = AutonomousBotConfig(
        symbol="ETH/USDT",
        timeframe="5m",
        interval_seconds=2,
        min_confidence_pct=70.0,
        risk_per_trade_pct=3.0,
    )

    status = await engine.start(config)
    assert status.is_running
    assert status.config.symbol == "ETH/USDT"
    assert status.config.interval_seconds == 2

    # Verify stop
    stopped_status = await engine.stop()
    assert not stopped_status.is_running


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_emergency_kill():
    mock_trading = MagicMock()
    mock_trading.get_positions.return_value = []
    mock_market = MagicMock()

    engine = AutonomousEngine(trading_service=mock_trading, market_service=mock_market)
    await engine.start()
    assert engine.is_running

    kill_status = await engine.emergency_kill()
    assert not kill_status.is_running
    assert any("EMERGENCY KILL-SWITCH" in log.message for log in kill_status.logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_cycle_execution():
    mock_market = MagicMock()
    mock_market.get_candles = AsyncMock(
        return_value=[
            Candle(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe.H1,
                timestamp=datetime.fromtimestamp(1700000000, tz=UTC),
                ohlcv=OHLCV(
                    open=Decimal("65000.0"),
                    high=Decimal("65500.0"),
                    low=Decimal("64800.0"),
                    close=Decimal("65200.0"),
                    volume=Decimal("120.5"),
                ),
                is_closed=True,
            )
        ]
    )

    mock_agent = MagicMock()
    mock_decision = CoordinatorDecision(
        symbol="BTC/USDT",
        final_signal=SignalDirection.BUY,
        aggregate_confidence=0.85,
        consensus_level=0.9,
        reasoning_summary="Strong bullish divergence and momentum breakout with multi-agent consensus alignment.",
        recommended_entry=65200.0,
        recommended_stop=63896.0,
        recommended_target=67808.0,
    )
    mock_agent.run_analysis = AsyncMock(return_value=mock_decision)

    mock_trading = MagicMock()
    mock_trading.get_portfolio.return_value = Portfolio(
        mode=ExecutionMode.PAPER,
        exchange="paper",
        total_balance=Decimal("10000.0"),
        available_balance=Decimal("10000.0"),
        locked_balance=Decimal("0.0"),
        positions=[],
    )
    mock_trading.get_positions.return_value = []
    mock_order = Order(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.003"),
        price=Decimal("65200.0"),
        exchange="paper",
        mode=ExecutionMode.PAPER,
        status=OrderStatus.FILLED,
    )
    mock_trading.place_order.return_value = mock_order

    engine = AutonomousEngine(
        agent_service=mock_agent,
        trading_service=mock_trading,
        market_service=mock_market,
    )
    engine.config.min_confidence_pct = 60.0
    engine.config.auto_execute = True

    await engine._execute_cycle()

    assert engine.total_cycles == 1
    assert engine.trades_executed == 1
    assert engine.last_signal == "BUY"
    assert engine.last_confidence == 85.0
    assert engine.last_price == 65200.0
    assert len(engine._logs) >= 1
    assert any("AUTO-ORDER EXECUTED" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_cycle_uses_live_ticker_price():
    """The engine must price the cycle from the real-time ticker, not the stale candle."""
    mock_market = MagicMock()
    mock_market.get_latest_price = AsyncMock(return_value=Decimal("70000.5"))
    mock_market.get_candles = AsyncMock()

    mock_agent = MagicMock()
    mock_agent.run_analysis = AsyncMock(
        return_value=CoordinatorDecision(
            symbol="BTC/USDT",
            final_signal=SignalDirection.NEUTRAL,
            aggregate_confidence=0.4,
            consensus_level=0.5,
            reasoning_summary="Mixed signals across agents; no clear directional edge detected right now.",
            recommended_entry=70000.0,
            recommended_stop=69000.0,
            recommended_target=72000.0,
        )
    )

    engine = AutonomousEngine(agent_service=mock_agent, market_service=mock_market)
    await engine._execute_cycle()

    assert engine.last_price == 70000.5
    mock_market.get_latest_price.assert_awaited_once()
    mock_market.get_candles.assert_not_called()
