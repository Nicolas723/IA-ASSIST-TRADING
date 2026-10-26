"""
Unit tests for AutonomousEngine and Autonomous Bot lifecycle.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.autonomous_engine import (
    AutonomousBotConfig,
    AutonomousCycleServices,
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
    Position,
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
        lambda: SimpleNamespace(
            real_trading_max_notional=Decimal("50"),
            correlation_guard_enabled=False,
        ),
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
        lambda: SimpleNamespace(
            real_trading_max_notional=Decimal("50"),
            correlation_guard_enabled=False,
        ),
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


def _correlation_candles(returns: list[float], symbol: str) -> list[Candle]:
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    start = 1700000000
    return [
        Candle(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=Timeframe.H1,
            timestamp=datetime.fromtimestamp(start + index * 3600, tz=UTC),
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


_PATTERN_A = [0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.01, -0.01]
_PATTERN_C = [0.01, 0.01, -0.01, -0.01, 0.01, 0.01, -0.01, -0.01]


def _correlation_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "real_trading_max_notional": Decimal("50"),
        "correlation_guard_enabled": True,
        "correlation_reduce_threshold": 0.75,
        "correlation_block_threshold": 0.95,
        "correlation_min_samples": 4,
        "correlation_lookback": 200,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _paper_guard_fixture(
    *,
    existing_returns: list[float],
) -> tuple[AutonomousEngine, MagicMock, MagicMock]:
    market = MagicMock()
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    market.get_candles = AsyncMock(
        side_effect=lambda symbol, **_kwargs: (
            _correlation_candles(_PATTERN_A, symbol)
            if symbol == "BTC/USDT"
            else _correlation_candles(existing_returns, symbol)
        )
    )
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
        recommended_units=0.01,
        recommended_notional_usd=650.0,
        risk_reward_ratio=2.0,
        potential_profit_usd=200.0,
        is_valid_setup=True,
        notes="",
    )
    trading = MagicMock()
    trading.get_portfolio.return_value = Portfolio(
        mode=ExecutionMode.PAPER,
        exchange=Exchange.BINANCE,
        total_balance=Decimal("10000"),
        available_balance=Decimal("10000"),
        locked_balance=Decimal("0"),
        positions=[],
    )
    trading.get_positions.return_value = [
        Position(
            symbol="ETH/USDT",
            exchange=Exchange.BINANCE,
            mode=ExecutionMode.PAPER,
            side=PositionSide.LONG,
            entry_price=Decimal("2000"),
            quantity=Decimal("1"),
            current_price=Decimal("2000"),
        )
    ]
    trading.place_order.return_value = Order(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        price=Decimal("65000"),
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        status=OrderStatus.FILLED,
    )

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        risk_service=risk,
        trading_service=trading,
    )
    engine.config = AutonomousBotConfig(execution_mode="paper", risk_per_trade_pct=1.0)
    return engine, risk, trading


@pytest.mark.unit
@pytest.mark.asyncio
async def test_buy_entry_is_reduced_by_correlation_guard(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: _correlation_settings(),
    )
    blended = [0.8 * left + 0.6 * right for left, right in zip(_PATTERN_A, _PATTERN_C, strict=True)]
    engine, risk, trading = _paper_guard_fixture(existing_returns=blended)

    await engine._execute_cycle()

    request = risk.calculate_position_size.call_args.args[0]
    assert request.correlation_multiplier == pytest.approx(0.75, abs=1e-2)
    trading.place_order.assert_called_once()
    assert any("Correlación positiva" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_buy_entry_is_blocked_by_correlation_guard(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: _correlation_settings(),
    )
    engine, risk, trading = _paper_guard_fixture(existing_returns=list(_PATTERN_A))

    await engine._execute_cycle()

    risk.calculate_position_size.assert_not_called()
    trading.place_order.assert_not_called()
    assert any(
        "bloqueada por correlación" in log.message and log.level == "WARNING"
        for log in engine._logs
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uncorrelated_entry_keeps_full_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: _correlation_settings(),
    )
    engine, risk, trading = _paper_guard_fixture(existing_returns=list(_PATTERN_C))

    await engine._execute_cycle()

    request = risk.calculate_position_size.call_args.args[0]
    assert request.correlation_multiplier == 1.0
    trading.place_order.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_correlation_guard_skips_market_lookups(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: _correlation_settings(correlation_guard_enabled=False),
    )
    engine, risk, trading = _paper_guard_fixture(existing_returns=list(_PATTERN_A))

    await engine._execute_cycle()

    market = engine.market_service
    assert market is not None
    market.get_candles.assert_not_awaited()
    request = risk.calculate_position_size.call_args.args[0]
    assert request.correlation_multiplier == 1.0
    trading.place_order.assert_called_once()


def _trend_candles(total_return_pct: float, symbol: str, points: int = 25) -> list[Candle]:
    step = total_return_pct / (points - 1)
    closes = [100.0]
    for _ in range(points - 1):
        closes.append(closes[-1] * (1.0 + step / 100.0))
    start = datetime.now(tz=UTC) - timedelta(hours=points)
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


def _neutral_decision(symbol: str) -> CoordinatorDecision:
    return CoordinatorDecision(
        symbol=symbol,
        final_signal=SignalDirection.NEUTRAL,
        aggregate_confidence=0.4,
        consensus_level=0.5,
        reasoning_summary="Mixed signals across agents; no clear directional edge detected right now.",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_scans_multiple_markets_and_analyzes_the_best_allowed() -> None:
    market = MagicMock()
    candles = {
        "BTC/USDT": _trend_candles(1.0, "BTC/USDT"),
        "ETH/USDT": _trend_candles(9.0, "ETH/USDT"),
        "SOL/USDT": _trend_candles(-5.0, "SOL/USDT"),
    }

    async def get_candles(symbol: str, **_kwargs: object) -> list[Candle]:
        return candles[symbol]

    market.get_candles = AsyncMock(side_effect=get_candles)
    market.get_latest_price = AsyncMock(
        side_effect=lambda symbol: Decimal("65000") if symbol == "BTC/USDT" else Decimal("3000")
    )
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_neutral_decision("ETH/USDT"))

    engine = AutonomousEngine(agent_service=agent, market_service=market)
    engine.config = AutonomousBotConfig(
        symbol="BTC/USDT",
        scan_symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        llm_analysis_interval_seconds=0,
    )

    await engine._execute_cycle()

    assert agent.run_analysis.call_args.kwargs["symbol"] == "ETH/USDT"
    assert engine.last_symbol == "ETH/USDT"
    assert engine.last_price == 3000.0
    assert any("Escaneo 3 mercados" in log.message for log in engine._logs)
    assert any("Candidato elegido: ETH/USDT" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_tracks_and_exits_positions_on_other_symbols() -> None:
    market = MagicMock()
    market.get_latest_price = AsyncMock(
        side_effect=lambda symbol: Decimal("65000") if symbol == "BTC/USDT" else Decimal("1000")
    )
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_neutral_decision("BTC/USDT"))

    position = Position(
        symbol="ETH/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        side=PositionSide.LONG,
        entry_price=Decimal("1200"),
        quantity=Decimal("1"),
        current_price=Decimal("1200"),
        stop_loss=Decimal("1100"),
    )
    trading = MagicMock()
    trading.get_positions.return_value = [position]
    trading.close_position.return_value = Order(
        symbol="ETH/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=Decimal("1000"),
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        status=OrderStatus.FILLED,
    )

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        trading_service=trading,
    )
    engine.config = AutonomousBotConfig(
        symbol="BTC/USDT",
        scan_symbols=["BTC/USDT"],
        llm_analysis_interval_seconds=0,
    )

    await engine._execute_cycle()

    trading.close_position.assert_called_once()
    assert trading.close_position.call_args.kwargs["current_market_price"] == 1000.0
    trading.update_market_prices.assert_called_once()
    assert trading.update_market_prices.call_args.args[0] == {
        "BTC/USDT": 65000.0,
        "ETH/USDT": 1000.0,
    }
    assert any("Auto-Exit" in log.message and log.symbol == "ETH/USDT" for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_allowlist_restricts_which_scanned_market_is_analyzed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market = MagicMock()
    candles = {
        "BTC/USDT": _trend_candles(1.0, "BTC/USDT"),
        "ETH/USDT": _trend_candles(9.0, "ETH/USDT"),
        "SOL/USDT": _trend_candles(5.0, "SOL/USDT"),
    }

    async def get_candles(symbol: str, **_kwargs: object) -> list[Candle]:
        return candles[symbol]

    market.get_candles = AsyncMock(side_effect=get_candles)
    market.get_latest_price = AsyncMock(return_value=Decimal("3000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_neutral_decision("SOL/USDT"))
    live = MagicMock()
    live.get_positions.return_value = []

    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: SimpleNamespace(
            real_trading_allowed_symbols=["BTC/USDT", "SOL/USDT"],
            real_trading_max_notional=Decimal("50"),
            correlation_guard_enabled=False,
        ),
    )

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        live_trading_service=live,
    )
    engine.config = AutonomousBotConfig(
        symbol="BTC/USDT",
        scan_symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        execution_mode="real",
        confirm_real_trading=True,
        llm_analysis_interval_seconds=0,
    )

    await engine._execute_cycle()

    # ETH has the strongest move but is not in the allowlist; SOL is the best
    # eligible candidate.
    assert agent.run_analysis.call_args.kwargs["symbol"] == "SOL/USDT"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_engine_skips_analysis_when_no_scanned_market_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market = MagicMock()
    candles = {
        "BTC/USDT": _trend_candles(1.0, "BTC/USDT"),
        "ETH/USDT": _trend_candles(9.0, "ETH/USDT"),
    }

    async def get_candles(symbol: str, **_kwargs: object) -> list[Candle]:
        return candles[symbol]

    market.get_candles = AsyncMock(side_effect=get_candles)
    market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    agent = MagicMock()
    agent.run_analysis = AsyncMock(return_value=_neutral_decision("BTC/USDT"))
    live = MagicMock()
    live.get_positions.return_value = []

    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: SimpleNamespace(
            real_trading_allowed_symbols=["XRP/USDT"],
            real_trading_max_notional=Decimal("50"),
            correlation_guard_enabled=False,
        ),
    )

    engine = AutonomousEngine(
        agent_service=agent,
        market_service=market,
        live_trading_service=live,
    )
    engine.config = AutonomousBotConfig(
        symbol="BTC/USDT",
        scan_symbols=["BTC/USDT", "ETH/USDT"],
        execution_mode="real",
        confirm_real_trading=True,
        llm_analysis_interval_seconds=0,
    )

    await engine._execute_cycle()

    agent.run_analysis.assert_not_called()
    assert any("allowlist" in log.message for log in engine._logs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_config_normalizes_scan_symbols_and_start_resolves_the_universe() -> None:
    config = AutonomousBotConfig(
        symbol="eth-usdt",
        scan_symbols=["sol_usdt", "ETH/USDT", "sol-usdt"],
    )
    assert config.symbol == "ETH/USDT"
    assert config.scan_symbols == ["SOL/USDT", "ETH/USDT"]

    engine = AutonomousEngine()
    await engine.start(AutonomousBotConfig(symbol="BTC/USDT"))
    try:
        assert engine.config.scan_symbols[0] == "BTC/USDT"
        assert len(engine.config.scan_symbols) > 1
    finally:
        await engine.stop()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emergency_kill_uses_each_position_mark_not_the_last_tick() -> None:
    position = Position(
        symbol="ETH/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        side=PositionSide.LONG,
        entry_price=Decimal("2000"),
        quantity=Decimal("1"),
        current_price=Decimal("2000"),
    )
    trading = MagicMock()
    trading.get_positions.return_value = [position]
    trading.close_position.return_value = Order(
        symbol="ETH/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=Decimal("2000"),
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        status=OrderStatus.FILLED,
    )

    engine = AutonomousEngine(trading_service=trading, market_service=MagicMock())
    engine.last_price = 65000.0
    engine.last_symbol = "BTC/USDT"

    await engine.emergency_kill()

    assert trading.close_position.call_args.kwargs["current_market_price"] == 2000.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cycle_never_reuses_the_request_scoped_services() -> None:
    """A background cycle must not touch the session the request just closed.

    Regression for ``IllegalStateChangeError``: the engine used to keep the
    ``MarketDataService``/``AgentService`` injected by ``/autonomous/start``,
    whose ``AsyncSession`` FastAPI closes as soon as the response is sent.
    """
    stale_market = MagicMock()
    stale_market.get_candles = AsyncMock(side_effect=RuntimeError("session is closed"))
    stale_market.get_latest_price = AsyncMock(side_effect=RuntimeError("session is closed"))
    stale_agent = MagicMock()
    stale_agent.run_analysis = AsyncMock(side_effect=RuntimeError("session is closed"))

    fresh_market = MagicMock()
    fresh_market.get_candles = AsyncMock(return_value=_trend_candles(1.0, "BTC/USDT"))
    fresh_market.get_latest_price = AsyncMock(return_value=Decimal("65000"))
    fresh_agent = MagicMock()
    fresh_agent.run_analysis = AsyncMock(return_value=_neutral_decision("BTC/USDT"))

    lifecycle: list[str] = []

    @asynccontextmanager
    async def factory():
        lifecycle.append("open")
        try:
            yield AutonomousCycleServices(
                market_service=fresh_market,
                agent_service=fresh_agent,
            )
        finally:
            lifecycle.append("close")

    engine = AutonomousEngine(
        agent_service=stale_agent,
        market_service=stale_market,
        cycle_service_factory=factory,
    )
    engine.config = AutonomousBotConfig(
        symbol="BTC/USDT",
        scan_symbols=["BTC/USDT"],
        llm_analysis_interval_seconds=0,
    )

    await engine._execute_cycle()

    assert lifecycle == ["open", "close"]
    assert engine.total_cycles == 1
    fresh_agent.run_analysis.assert_awaited_once()
    stale_agent.run_analysis.assert_not_called()
    stale_market.get_candles.assert_not_called()
    stale_market.get_latest_price.assert_not_called()
