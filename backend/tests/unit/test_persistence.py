"""
Unit tests for the persistence layer (ORM models + repositories).

Runs against an in-memory SQLite database so the mappers and repository
round-trips are exercised without needing PostgreSQL. SQLite is dialect-portable
for this schema (Uuid / JSON / DECIMAL are all supported).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.application.services.hydration import hydrate_paper_exchange
from src.application.services.journal import record_execution
from src.application.services.trading_service import TradingService
from src.domain.agents.entities import (
    AgentOutput,
    AgentType,
    CoordinatorDecision,
    KeyFactor,
    SignalDirection,
)
from src.domain.market.entities import Exchange, Timeframe
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    Trade,
)
from src.infrastructure.exchange.paper import PaperExchange, PlaceOrderRequest
from src.infrastructure.persistence.database import Base
from src.infrastructure.persistence.models.market import CandleModel
from src.infrastructure.persistence.repositories.candle_repository import (
    TimescaleCandleRepository,
)
from src.infrastructure.persistence.repositories.decision_repository import (
    SqlDecisionRepository,
)
from src.infrastructure.persistence.repositories.trading_repository import (
    SqlTradingRepository,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session():
    """A fresh in-memory SQLite database with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session

    await engine.dispose()


def _make_order(**overrides) -> Order:
    defaults = {
        "symbol": "BTC/USDT",
        "exchange": Exchange.PAPER,
        "mode": ExecutionMode.PAPER,
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.5"),
        "status": OrderStatus.FILLED,
        "filled_quantity": Decimal("0.5"),
        "average_fill_price": Decimal("65000"),
        "commission": Decimal("1.25"),
        "notes": "unit test",
    }
    defaults.update(overrides)
    return Order(**defaults)


# ---------------------------------------------------------------------------
# Trading repository
# ---------------------------------------------------------------------------


async def test_order_round_trip(session):
    repo = SqlTradingRepository(session)
    order = _make_order()

    await repo.save_order(order)
    loaded = await repo.get_order(order.id)

    assert loaded is not None
    assert loaded.id == order.id
    assert loaded.symbol == "BTC/USDT"
    assert loaded.exchange == Exchange.PAPER
    assert loaded.mode == ExecutionMode.PAPER
    assert loaded.side == OrderSide.BUY
    assert loaded.order_type == OrderType.MARKET
    assert loaded.status == OrderStatus.FILLED
    assert loaded.quantity == Decimal("0.5")
    assert loaded.average_fill_price == Decimal("65000")
    assert loaded.commission == Decimal("1.25")


async def test_list_orders_filters_and_orders(session):
    repo = SqlTradingRepository(session)
    await repo.save_order(_make_order(symbol="BTC/USDT"))
    await repo.save_order(_make_order(symbol="ETH/USDT"))
    await repo.save_order(_make_order(symbol="BTC/USDT", mode=ExecutionMode.BACKTEST))

    paper_btc = await repo.list_orders(mode=ExecutionMode.PAPER, symbol="BTC/USDT")
    assert len(paper_btc) == 1
    assert paper_btc[0].symbol == "BTC/USDT"
    assert paper_btc[0].mode == ExecutionMode.PAPER

    all_btc = await repo.list_orders(symbol="BTC/USDT")
    assert len(all_btc) == 2


async def test_order_upsert_updates_existing(session):
    repo = SqlTradingRepository(session)
    order = _make_order(status=OrderStatus.OPEN, filled_quantity=Decimal("0"))
    await repo.save_order(order)

    order.status = OrderStatus.FILLED
    order.filled_quantity = Decimal("0.5")
    await repo.save_order(order)

    loaded = await repo.get_order(order.id)
    assert loaded is not None
    assert loaded.status == OrderStatus.FILLED
    assert loaded.filled_quantity == Decimal("0.5")


async def test_trade_round_trip(session):
    repo = SqlTradingRepository(session)
    trade = Trade(
        order_id=uuid4(),
        symbol="BTC/USDT",
        exchange=Exchange.PAPER,
        mode=ExecutionMode.PAPER,
        side=OrderSide.BUY,
        quantity=Decimal("0.5"),
        price=Decimal("65000"),
        commission=Decimal("1.25"),
        commission_asset="USDT",
    )
    await repo.save_trade(trade)

    trades = await repo.list_trades(symbol="BTC/USDT")
    assert len(trades) == 1
    assert trades[0].order_id == trade.order_id
    assert trades[0].price == Decimal("65000")
    assert trades[0].notional_value == Decimal("32500.0")


async def test_position_upsert_and_delete(session):
    repo = SqlTradingRepository(session)
    position = Position(
        symbol="ETH/USDT",
        exchange=Exchange.PAPER,
        mode=ExecutionMode.PAPER,
        side=PositionSide.LONG,
        entry_price=Decimal("3000"),
        quantity=Decimal("2"),
        current_price=Decimal("3100"),
    )
    await repo.upsert_position(position)

    positions = await repo.list_positions(mode=ExecutionMode.PAPER)
    assert len(positions) == 1
    assert positions[0].current_price == Decimal("3100")
    assert positions[0].unrealized_pnl == Decimal("200")

    await repo.delete_position(position.id)
    assert await repo.list_positions() == []


async def test_portfolio_snapshot(session):
    repo = SqlTradingRepository(session)
    portfolio = Portfolio(
        mode=ExecutionMode.PAPER,
        exchange=Exchange.PAPER,
        total_balance=Decimal("10500"),
        available_balance=Decimal("5000"),
        locked_balance=Decimal("0"),
        peak_value=Decimal("11000"),
        total_realized_pnl=Decimal("500"),
    )
    await repo.save_portfolio_snapshot(portfolio, Exchange.PAPER)

    latest = await repo.latest_portfolio_snapshot(ExecutionMode.PAPER, Exchange.PAPER)
    assert latest is not None
    assert latest.total_balance == Decimal("10500")
    assert latest.peak_value == Decimal("11000")
    assert latest.total_realized_pnl == Decimal("500")
    assert latest.position_count == 0

    # A later snapshot wins
    portfolio.total_balance = Decimal("10800")
    portfolio.updated_at = datetime.now(UTC)
    await repo.save_portfolio_snapshot(portfolio, Exchange.PAPER)
    latest = await repo.latest_portfolio_snapshot(ExecutionMode.PAPER, Exchange.PAPER)
    assert latest is not None
    assert latest.total_balance == Decimal("10800")


# ---------------------------------------------------------------------------
# Decision (audit) repository
# ---------------------------------------------------------------------------


def _make_decision(symbol: str = "BTC/USDT") -> CoordinatorDecision:
    output = AgentOutput(
        task_id=uuid4(),
        agent_type=AgentType.TECHNICAL_ANALYST,
        symbol=symbol,
        signal=SignalDirection.BUY,
        confidence=0.8,
        reasoning="RSI is recovering from oversold while MACD prints a bullish crossover on the 1h chart.",
        key_factors=[
            KeyFactor(name="RSI", impact="bullish", weight=0.6, description="Oversold bounce")
        ],
        llm_provider="openai",
        llm_model="gpt-4o",
        prompt_sent="Analyze BTC/USDT",
        llm_response="BUY with confidence 0.8",
    )
    return CoordinatorDecision(
        symbol=symbol,
        final_signal=SignalDirection.BUY,
        aggregate_confidence=0.8,
        agent_outputs=[output],
        agent_weights={"technical_analyst": 1.0},
        reasoning_summary="Committee consensus leans bullish driven by technical confluence and risk containment.",
        consensus_level=0.9,
        recommended_entry=Decimal("65000"),
        recommended_stop=Decimal("63000"),
        recommended_target=Decimal("70000"),
        risk_approved=True,
        action_recommendation="Buy on pullback with 2% risk.",
    )


async def test_decision_round_trip_preserves_full_trail(session):
    repo = SqlDecisionRepository(session)
    decision = _make_decision()

    await repo.save_decision(decision)
    loaded = await repo.get_decision(decision.id)

    assert loaded is not None
    assert loaded.id == decision.id
    assert loaded.final_signal == SignalDirection.BUY
    assert loaded.aggregate_confidence == 0.8
    assert loaded.reasoning_summary == decision.reasoning_summary
    assert loaded.recommended_entry == Decimal("65000")
    assert loaded.risk_approved is True

    # Full nested trail is preserved
    assert len(loaded.agent_outputs) == 1
    restored = loaded.agent_outputs[0]
    assert restored.agent_type == AgentType.TECHNICAL_ANALYST
    assert restored.confidence == 0.8
    assert restored.prompt_sent == "Analyze BTC/USDT"
    assert restored.llm_response == "BUY with confidence 0.8"
    assert restored.key_factors[0].name == "RSI"


async def test_list_decisions_filter_by_symbol(session):
    repo = SqlDecisionRepository(session)
    await repo.save_decision(_make_decision("BTC/USDT"))
    await repo.save_decision(_make_decision("ETH/USDT"))

    btc = await repo.list_decisions(symbol="BTC/USDT")
    assert len(btc) == 1
    assert btc[0].symbol == "BTC/USDT"

    everything = await repo.list_decisions()
    assert len(everything) == 2


# ---------------------------------------------------------------------------
# Journal reconciliation + paper exchange rehydration
# ---------------------------------------------------------------------------


def _make_position(symbol: str = "BTC/USDT") -> Position:
    return Position(
        symbol=symbol,
        exchange=Exchange.PAPER,
        mode=ExecutionMode.PAPER,
        side=PositionSide.LONG,
        entry_price=Decimal("65000"),
        quantity=Decimal("0.1"),
        current_price=Decimal("66000"),
    )


async def test_sync_positions_drops_closed_positions(session):
    repo = SqlTradingRepository(session)
    keep = _make_position("BTC/USDT")
    closed = _make_position("ETH/USDT")

    await repo.sync_positions(ExecutionMode.PAPER, [keep, closed])
    assert len(await repo.list_positions(mode=ExecutionMode.PAPER)) == 2

    await repo.sync_positions(ExecutionMode.PAPER, [keep])
    remaining = await repo.list_positions(mode=ExecutionMode.PAPER)
    assert [p.id for p in remaining] == [keep.id]


async def test_record_execution_persists_full_state(session):
    repo = SqlTradingRepository(session)
    exchange = PaperExchange()
    service = TradingService(paper_exchange=exchange)

    order = service.place_order(
        req=PlaceOrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            stop_loss=60000.0,
            take_profit=70000.0,
        ),
        current_market_price=65000.0,
    )

    assert await record_execution(repo, service, order) is True

    assert len(await repo.list_orders(mode=ExecutionMode.PAPER)) == 1
    assert len(await repo.list_trades(mode=ExecutionMode.PAPER)) == 1
    assert len(await repo.list_positions(mode=ExecutionMode.PAPER)) == 1

    snapshot = await repo.latest_portfolio_snapshot(ExecutionMode.PAPER, Exchange.PAPER)
    assert snapshot is not None
    assert snapshot.available_balance == exchange.available_balance


async def test_hydrate_restores_paper_exchange_after_restart(session):
    repo = SqlTradingRepository(session)

    # --- "Before restart": trade and persist -------------------------------
    original = PaperExchange()
    service = TradingService(paper_exchange=original)
    order = service.place_order(
        req=PlaceOrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            stop_loss=60000.0,
            take_profit=70000.0,
        ),
        current_market_price=65000.0,
    )
    await record_execution(repo, service, order)
    expected_balance = original.available_balance

    # --- "After restart": a brand-new in-memory exchange -------------------
    revived = PaperExchange()
    report = await hydrate_paper_exchange(revived, repo)

    assert report.positions_restored == 1
    assert report.orders_restored == 1
    assert report.trades_restored == 1
    assert report.balance_restored is True

    assert len(revived.positions) == 1
    assert len(revived.orders) == 1
    assert len(revived.trades) == 1
    assert revived.available_balance == expected_balance

    portfolio = revived.get_portfolio()
    assert portfolio.position_count == 1
    assert portfolio.total_balance > 0
    assert portfolio.available_balance == expected_balance


async def test_hydrate_is_noop_on_empty_journal(session):
    repo = SqlTradingRepository(session)
    exchange = PaperExchange()
    report = await hydrate_paper_exchange(exchange, repo)

    assert report.positions_restored == 0
    assert report.balance_restored is False
    assert exchange.available_balance == Decimal("10000.0")


async def test_clear_journal_wipes_mode(session):
    repo = SqlTradingRepository(session)
    exchange = PaperExchange()
    service = TradingService(paper_exchange=exchange)
    order = service.place_order(
        req=PlaceOrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
        ),
        current_market_price=65000.0,
    )
    await record_execution(repo, service, order)

    await repo.clear_journal(ExecutionMode.PAPER)

    assert await repo.list_orders(mode=ExecutionMode.PAPER) == []
    assert await repo.list_trades(mode=ExecutionMode.PAPER) == []
    assert await repo.list_positions(mode=ExecutionMode.PAPER) == []
    assert await repo.latest_portfolio_snapshot(ExecutionMode.PAPER, Exchange.PAPER) is None


# ---------------------------------------------------------------------------
# Candle repository (dialect-portable query path)
# ---------------------------------------------------------------------------


async def test_candle_repository_get_candles(session):
    repo = TimescaleCandleRepository(session)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    session.add(
        CandleModel(
            time=timestamp,
            symbol="BTC/USDT",
            exchange="binance",
            timeframe="1h",
            open=100.0,
            high=110.0,
            low=90.0,
            close=105.0,
            volume=12.5,
            is_closed=True,
        )
    )
    await session.commit()

    candles = await repo.get_candles(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.H1,
        start=timestamp,
        end=timestamp,
        limit=10,
    )

    assert len(candles) == 1
    assert candles[0].symbol == "BTC/USDT"
    assert candles[0].ohlcv.close == Decimal("105")


async def test_candle_repository_save_empty_is_noop(session):
    repo = TimescaleCandleRepository(session)
    await repo.save_candles([])  # returns before touching the DB
