"""
Unit tests for AutonomousEngine and Autonomous Bot lifecycle.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.autonomous_engine import (
    AutonomousBotConfig,
    AutonomousEngine,
)
from src.domain.agents.entities import (
    CoordinatorDecision,
    RiskFlag,
    RiskFlagSeverity,
    SignalDirection,
)
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.risk.entities import PositionSizeResult
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    PositionSide,
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
def test_autonomous_config_requires_minimum_reward_risk_ratio():
    with pytest.raises(ValueError, match=r"at least 1\.5 times"):
        AutonomousBotConfig(stop_loss_pct=2.0, take_profit_pct=2.9)

    config = AutonomousBotConfig(stop_loss_pct=2.0, take_profit_pct=3.0)
    assert config.take_profit_pct / config.stop_loss_pct == 1.5


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
        risk_approved=True,
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
    placed_request = mock_trading.place_order.call_args.kwargs["req"]
    assert placed_request.coordinator_decision_id == mock_decision.id
    assert placed_request.client_order_id == f"ata-{mock_decision.id.hex[:24]}"
    assert placed_request.quantity == 0.007669


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_engine_abstains_when_committee_risk_approval_is_false():
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(
        return_value=CoordinatorDecision(
            symbol="BTC/USDT",
            final_signal=SignalDirection.STRONG_BUY,
            aggregate_confidence=0.95,
            consensus_level=1.0,
            reasoning_summary=(
                "The committee sees a strong bullish signal, but the risk manager vetoed entry."
            ),
            risk_approved=False,
            all_risk_flags=[
                RiskFlag(
                    code="RISK_VETO",
                    description="Volatility exceeds the safe limit.",
                    severity=RiskFlagSeverity.CRITICAL,
                )
            ],
        )
    )
    trading = MagicMock()

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        trading_service=trading,
    )
    engine.config.auto_execute = True

    await engine._execute_cycle()

    trading.place_order.assert_not_called()
    assert any("abstendida por Risk Manager" in log.message for log in engine._logs)


def _sell_decision() -> CoordinatorDecision:
    return CoordinatorDecision(
        symbol="BTC/USDT",
        final_signal=SignalDirection.STRONG_SELL,
        aggregate_confidence=0.9,
        consensus_level=0.9,
        reasoning_summary="Strong bearish momentum and unanimous committee consensus indicate an exit.",
        risk_approved=True,
        recommended_entry=65_000,
        recommended_stop=66_300,
        recommended_target=62_400,
    )


def _paper_portfolio() -> Portfolio:
    return Portfolio(
        mode=ExecutionMode.PAPER,
        exchange="paper",
        total_balance=Decimal("10000"),
        available_balance=Decimal("10000"),
        locked_balance=Decimal("0"),
        positions=[],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_sell_signal_never_sells_untracked_wallet_balance():
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_sell_decision())
    trading = MagicMock()
    trading.get_portfolio.return_value = _paper_portfolio()
    trading.get_positions.return_value = []

    engine = AutonomousEngine(agent_service=agent, trading_service=trading, market_service=market)
    await engine._execute_cycle()

    trading.place_order.assert_not_called()
    trading.close_position.assert_not_called()
    assert any("saldo externo" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_autonomous_sell_signal_closes_only_its_tracked_position():
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_sell_decision())
    position_id = "tracked-position"
    position = SimpleNamespace(
        id=position_id,
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        exchange=Exchange.BINANCE,
        stop_loss=None,
        take_profit=None,
    )
    trading = MagicMock()
    trading.get_portfolio.return_value = _paper_portfolio()
    trading.get_positions.return_value = [position]
    trading.close_position.return_value = Order(
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        price=Decimal("65000"),
        exchange=Exchange.PAPER,
        mode=ExecutionMode.PAPER,
        status=OrderStatus.FILLED,
    )

    engine = AutonomousEngine(agent_service=agent, trading_service=trading, market_service=market)
    await engine._execute_cycle()

    trading.close_position.assert_called_once_with(
        position_id=position_id,
        current_market_price=65000.0,
        mode=ExecutionMode.PAPER,
    )
    trading.place_order.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_order_quantity_is_capped_to_max_notional(monkeypatch) -> None:
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(
        return_value=CoordinatorDecision(
            symbol="BTC/USDT",
            final_signal=SignalDirection.BUY,
            aggregate_confidence=0.85,
            consensus_level=0.9,
            reasoning_summary="Bullish momentum with committee consensus and risk approval.",
            risk_approved=True,
            recommended_entry=65000.0,
            recommended_stop=63700.0,
            recommended_target=67600.0,
        )
    )
    risk = MagicMock()
    risk.calculate_position_size.return_value = PositionSizeResult(
        symbol="BTC/USDT",
        account_balance=10000.0,
        risk_percentage=1.0,
        risk_amount_usd=100.0,
        price_risk_per_unit=1300.0,
        price_risk_pct=2.0,
        recommended_units=0.1,
        recommended_notional_usd=6500.0,
        risk_reward_ratio=2.0,
        potential_profit_usd=200.0,
        is_valid_setup=True,
        notes="",
    )
    live = MagicMock()
    live.get_portfolio.return_value = Portfolio(
        mode=ExecutionMode.REAL,
        exchange=Exchange.BINANCE,
        total_balance=Decimal("10000"),
        available_balance=Decimal("10000"),
        locked_balance=Decimal("0"),
        positions=[],
    )
    live.get_positions.return_value = []
    live.preflight_reconciliation = AsyncMock(return_value=(True, None))
    live.place_order = AsyncMock(
        return_value=Order(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.000769"),
            price=Decimal("65000"),
            exchange=Exchange.BINANCE,
            mode=ExecutionMode.REAL,
            status=OrderStatus.FILLED,
        )
    )
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: SimpleNamespace(real_trading_max_notional=Decimal("50")),
    )

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        risk_service=risk,
        live_trading_service=live,
    )
    engine.config = AutonomousBotConfig(
        execution_mode="real",
        confirm_real_trading=True,
        risk_per_trade_pct=1.0,
    )

    await engine._execute_cycle()

    placed = live.place_order.call_args.kwargs["req"]
    assert placed.quantity == 0.000769
    assert any("Cantidad ajustada" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_entry_is_suspended_when_reconciliation_fails(monkeypatch) -> None:
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(
        return_value=CoordinatorDecision(
            symbol="BTC/USDT",
            final_signal=SignalDirection.BUY,
            aggregate_confidence=0.85,
            consensus_level=0.9,
            reasoning_summary="Bullish momentum with committee consensus and risk approval.",
            risk_approved=True,
            recommended_entry=65000.0,
            recommended_stop=63700.0,
            recommended_target=67600.0,
        )
    )
    live = MagicMock()
    live.get_portfolio.return_value = Portfolio(
        mode=ExecutionMode.REAL,
        exchange=Exchange.BINANCE,
        total_balance=Decimal("10000"),
        available_balance=Decimal("10000"),
        locked_balance=Decimal("0"),
        positions=[],
    )
    live.get_positions.return_value = []
    live.preflight_reconciliation = AsyncMock(return_value=(False, "exchange unreachable"))
    live.place_order = AsyncMock()
    risk = MagicMock()
    risk.calculate_position_size.return_value = PositionSizeResult(
        symbol="BTC/USDT",
        account_balance=10000.0,
        risk_percentage=1.0,
        risk_amount_usd=100.0,
        price_risk_per_unit=1300.0,
        price_risk_pct=2.0,
        recommended_units=0.1,
        recommended_notional_usd=6500.0,
        risk_reward_ratio=2.0,
        potential_profit_usd=200.0,
        is_valid_setup=True,
        notes="",
    )
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: SimpleNamespace(real_trading_max_notional=Decimal("50")),
    )

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        risk_service=risk,
        live_trading_service=live,
    )
    engine.config = AutonomousBotConfig(
        execution_mode="real",
        confirm_real_trading=True,
        risk_per_trade_pct=1.0,
    )

    await engine._execute_cycle()

    live.preflight_reconciliation.assert_awaited_once()
    live.place_order.assert_not_called()
    assert any("Entrada suspendida" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_exit_is_not_blocked_by_reconciliation_failure() -> None:
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_sell_decision())
    position = SimpleNamespace(
        id="tracked-position",
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        exchange=Exchange.BINANCE,
        stop_loss=None,
        take_profit=None,
    )
    live = MagicMock()
    live.get_portfolio.return_value = Portfolio(
        mode=ExecutionMode.REAL,
        exchange=Exchange.BINANCE,
        total_balance=Decimal("10000"),
        available_balance=Decimal("10000"),
        locked_balance=Decimal("0"),
        positions=[],
    )
    live.get_positions.return_value = [position]
    live.preflight_reconciliation = AsyncMock(return_value=(False, "exchange unreachable"))
    live.close_position = AsyncMock(
        return_value=Order(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
            price=Decimal("65000"),
            exchange=Exchange.BINANCE,
            mode=ExecutionMode.REAL,
            status=OrderStatus.FILLED,
        )
    )

    engine = AutonomousEngine(agent_service=agent, market_service=market, live_trading_service=live)
    engine.config = AutonomousBotConfig(execution_mode="real", confirm_real_trading=True)

    await engine._execute_cycle()

    live.close_position.assert_awaited_once()
    live.preflight_reconciliation.assert_not_awaited()
    live.place_order.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_committee_analysis_is_throttled_between_fast_cycles() -> None:
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(
        return_value=CoordinatorDecision(
            symbol="BTC/USDT",
            final_signal=SignalDirection.NEUTRAL,
            aggregate_confidence=0.4,
            consensus_level=0.5,
            reasoning_summary="Mixed signals across agents; no clear directional edge detected right now.",
        )
    )

    engine = AutonomousEngine(agent_service=agent, market_service=market)
    engine.config = AutonomousBotConfig(llm_analysis_interval_seconds=1800)

    await engine._execute_cycle()
    await engine._execute_cycle()

    # The first cycle analyzes; the second one still tracks positions but
    # defers the expensive committee run.
    agent.run_analysis.assert_awaited_once()
    assert engine.total_cycles == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_analysis_interval_runs_the_committee_every_cycle() -> None:
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(
        return_value=CoordinatorDecision(
            symbol="BTC/USDT",
            final_signal=SignalDirection.NEUTRAL,
            aggregate_confidence=0.4,
            consensus_level=0.5,
            reasoning_summary="Mixed signals across agents; no clear directional edge detected right now.",
        )
    )

    engine = AutonomousEngine(agent_service=agent, market_service=market)
    engine.config = AutonomousBotConfig(llm_analysis_interval_seconds=0)

    await engine._execute_cycle()
    await engine._execute_cycle()

    assert agent.run_analysis.await_count == 2
