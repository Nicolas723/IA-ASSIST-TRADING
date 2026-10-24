"""Unit tests for decision attribution and continuous-learning metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from src.application.services.learning_service import LearningService
from src.domain.agents.entities import (
    AgentOutput,
    AgentType,
    CoordinatorDecision,
    SignalDirection,
)
from src.domain.analytics.entities import (
    DecisionOutcome,
    DecisionOutcomeStatus,
)
from src.domain.market.entities import Exchange
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeOutcome,
)
from src.infrastructure.exchange.paper import PaperExchange, PlaceOrderRequest


def _agent(
    agent_type: AgentType,
    signal: SignalDirection,
    confidence: float = 0.8,
    latency_ms: float = 100.0,
) -> AgentOutput:
    return AgentOutput(
        task_id=uuid4(),
        agent_type=agent_type,
        symbol="BTC/USDT",
        signal=signal,
        confidence=confidence,
        reasoning=(
            "Synthetic test reasoning long enough to satisfy the mandatory audit explanation field."
        ),
        processing_time_ms=latency_ms,
    )


def _decision(
    signal: SignalDirection = SignalDirection.BUY,
    confidence: float = 0.8,
    outputs: list[AgentOutput] | None = None,
    symbol: str = "BTC/USDT",
) -> CoordinatorDecision:
    return CoordinatorDecision(
        symbol=symbol,
        final_signal=signal,
        aggregate_confidence=confidence,
        agent_outputs=outputs or [_agent(AgentType.TECHNICAL_ANALYST, SignalDirection.BUY)],
        reasoning_summary=(
            "Committee consensus based on technical confluence, risk containment "
            "and a tested directional thesis."
        ),
        consensus_level=0.9,
        risk_approved=True,
        action_recommendation="Execute the tested directional setup.",
    )


def _order(
    decision_id,
    side: OrderSide,
    quantity: str,
    price: str,
    commission: str = "0",
    commission_asset: str = "USDT",
    symbol: str = "BTC/USDT",
    mode: ExecutionMode = ExecutionMode.PAPER,
    status: OrderStatus = OrderStatus.FILLED,
) -> Order:
    now = datetime.now(UTC)
    qty = Decimal(quantity)
    return Order(
        id=uuid4(),
        symbol=symbol,
        exchange=Exchange.PAPER,
        mode=mode,
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
        filled_quantity=qty,
        average_fill_price=Decimal(price),
        commission=Decimal(commission),
        commission_asset=commission_asset,
        status=status,
        coordinator_decision_id=decision_id,
        created_at=now,
        filled_at=now,
    )


def test_winning_decision_is_attributed_with_net_pnl() -> None:
    decision = _decision()
    buy = _order(decision.id, OrderSide.BUY, "1", "100", "1")
    sell = _order(decision.id, OrderSide.SELL, "1", "120", "1.2")

    outcome = LearningService().build_outcomes([decision], [buy, sell])[0]

    assert outcome.status == DecisionOutcomeStatus.CLOSED
    assert outcome.entry_price == Decimal("100")
    assert outcome.exit_price == Decimal("120")
    assert outcome.quantity == Decimal("1")
    assert outcome.gross_pnl == Decimal("20")
    assert outcome.fees == Decimal("2.2")
    assert outcome.net_pnl == Decimal("17.8")
    assert outcome.net_pnl_pct == 17.8
    assert outcome.outcome == TradeOutcome.WIN
    assert outcome.decision_hit is True
    assert outcome.agent_hits["technical_analyst"] is True


def test_losing_trade_and_agent_miss() -> None:
    decision = _decision()
    buy = _order(decision.id, OrderSide.BUY, "1", "100")
    sell = _order(decision.id, OrderSide.SELL, "1", "90")

    outcome = LearningService().build_outcomes([decision], [buy, sell])[0]

    assert outcome.status == DecisionOutcomeStatus.CLOSED
    assert outcome.outcome == TradeOutcome.LOSS
    assert outcome.net_pnl == Decimal("-10")
    assert outcome.decision_hit is False
    assert outcome.agent_hits["technical_analyst"] is False


def test_open_and_unexecuted_decisions_are_not_resolved() -> None:
    decision = _decision()
    buy = _order(decision.id, OrderSide.BUY, "1", "100")

    outcomes = LearningService().build_outcomes([decision], [buy])
    assert outcomes[0].status == DecisionOutcomeStatus.OPEN
    assert outcomes[0].net_pnl == Decimal("0")
    assert outcomes[0].decision_hit is None
    assert outcomes[0].agent_hits["technical_analyst"] is None

    empty = LearningService().build_outcomes([_decision()], [])
    assert empty[0].status == DecisionOutcomeStatus.UNEXECUTED
    assert empty[0].order_count == 0


def test_partial_exit_stays_open_with_realised_pnl() -> None:
    decision = _decision()
    buy = _order(decision.id, OrderSide.BUY, "1", "100")
    sell = _order(decision.id, OrderSide.SELL, "0.5", "120")

    outcome = LearningService().build_outcomes([decision], [buy, sell])[0]

    assert outcome.status == DecisionOutcomeStatus.OPEN
    assert outcome.quantity == Decimal("0.5")
    assert outcome.net_pnl == Decimal("10")
    assert outcome.outcome == TradeOutcome.OPEN
    assert "Partial exit" in outcome.note


def test_base_asset_commission_is_converted_to_quote() -> None:
    decision = _decision()
    buy = _order(
        decision.id,
        OrderSide.BUY,
        "1",
        "100",
        commission="0.01",
        commission_asset="BTC",
    )
    sell = _order(decision.id, OrderSide.SELL, "1", "120", "1.2")

    outcome = LearningService().build_outcomes([decision], [buy, sell])[0]

    assert outcome.fees == Decimal("2.2")
    assert outcome.net_pnl == Decimal("17.8")


def test_sell_only_execution_is_not_scored_as_an_entry() -> None:
    decision = _decision(signal=SignalDirection.SELL)
    sell = _order(decision.id, OrderSide.SELL, "1", "100")

    outcome = LearningService().build_outcomes([decision], [sell])[0]

    assert outcome.status == DecisionOutcomeStatus.UNEXECUTED
    assert outcome.order_count == 1
    assert "Exit-only" in outcome.note


def test_summary_groups_agents_signals_symbols_and_confidence() -> None:
    win = _decision(
        confidence=0.8,
        outputs=[_agent(AgentType.TECHNICAL_ANALYST, SignalDirection.BUY)],
    )
    loss = _decision(
        signal=SignalDirection.SELL,
        confidence=0.9,
        outputs=[
            _agent(AgentType.PRICE_ACTION_ANALYST, SignalDirection.BUY),
            _agent(AgentType.RISK_MANAGER, SignalDirection.HOLD),
            _agent(AgentType.SENTIMENT_ANALYST, SignalDirection.NEUTRAL),
        ],
        symbol="ETH/USDT",
    )
    orders = [
        _order(win.id, OrderSide.BUY, "1", "100"),
        _order(win.id, OrderSide.SELL, "1", "120"),
        _order(loss.id, OrderSide.BUY, "1", "100", symbol="ETH/USDT"),
        _order(loss.id, OrderSide.SELL, "1", "90", symbol="ETH/USDT"),
        _order(None, OrderSide.BUY, "1", "100"),
    ]

    summary = LearningService().build_summary([win, loss], orders)

    assert summary.total_decisions == 2
    assert summary.executed == 2
    assert summary.resolved == 2
    assert summary.win_rate_pct == 50.0
    assert summary.net_pnl == Decimal("10")
    assert summary.profit_factor == 2.0
    assert summary.unattributed_orders == 1

    agents = {item.agent_type: item for item in summary.by_agent}
    assert agents["technical_analyst"].correct == 1
    assert agents["technical_analyst"].hit_rate_pct == 100.0
    assert agents["price_action_analyst"].incorrect == 1
    assert agents["sentiment_analyst"].abstained == 1
    assert "risk_manager" not in agents

    signals = {item.signal: item for item in summary.by_signal}
    assert signals["buy"].correct == 1
    assert signals["sell"].correct == 1
    assert signals["sell"].net_pnl == Decimal("-10")

    symbols = {item.symbol: item for item in summary.by_symbol}
    assert symbols["BTC/USDT"].win_rate_pct == 100.0
    assert symbols["ETH/USDT"].win_rate_pct == 0.0

    buckets = {(item.lower, item.upper): item for item in summary.confidence_buckets}
    assert buckets[(0.8, 0.9)].decisions == 1
    assert buckets[(0.8, 0.9)].correct == 1
    assert buckets[(0.9, 1.0)].decisions == 1
    assert buckets[(0.9, 1.0)].correct == 1


def test_paper_exchange_propagates_decision_to_position_and_closing_trade() -> None:
    exchange = PaperExchange()
    decision_id = uuid4()

    buy = exchange.place_order(
        PlaceOrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            stop_loss=90,
            take_profit=120,
            coordinator_decision_id=decision_id,
        ),
        current_market_price=100,
    )
    assert buy.coordinator_decision_id == decision_id

    position = next(iter(exchange.positions.values()))
    assert position.coordinator_decision_id == decision_id

    sell = exchange.close_position(position.id, current_market_price=110)
    assert sell is not None
    assert sell.coordinator_decision_id == decision_id

    trade = next(item for item in exchange.trades if item.order_id == sell.id)
    assert trade.coordinator_decision_id == decision_id


def test_risk_manager_is_excluded_and_neutral_agents_abstain() -> None:
    decision = _decision(
        outputs=[
            _agent(AgentType.TECHNICAL_ANALYST, SignalDirection.BUY),
            _agent(AgentType.RISK_MANAGER, SignalDirection.HOLD),
            _agent(AgentType.SENTIMENT_ANALYST, SignalDirection.NEUTRAL),
        ]
    )
    orders = [
        _order(decision.id, OrderSide.BUY, "1", "100"),
        _order(decision.id, OrderSide.SELL, "1", "110"),
    ]

    summary = LearningService().build_summary([decision], orders)

    agents = {item.agent_type: item for item in summary.by_agent}
    assert set(agents) == {"technical_analyst", "sentiment_analyst"}
    assert agents["technical_analyst"].correct == 1
    assert agents["sentiment_analyst"].abstained == 1
    assert agents["sentiment_analyst"].hit_rate_pct == 0.0


def test_score_bucket_marks_small_samples_as_insufficient() -> None:
    now = datetime.now(UTC)
    outcomes = [
        DecisionOutcome(
            decision_id=uuid4(),
            decision_created_at=now,
            symbol="BTC/USDT",
            signal="buy",
            confidence=0.85,
            status=DecisionOutcomeStatus.CLOSED,
            decision_hit=True,
        )
        for _ in range(29)
    ]

    buckets = LearningService()._confidence_buckets(outcomes)
    high_score_bucket = next(bucket for bucket in buckets if bucket.lower == 0.8)

    assert high_score_bucket.decisions == 29
    assert high_score_bucket.sample_sufficient is False
    assert high_score_bucket.minimum_sample_size == 30


def test_score_bucket_marks_larger_samples_exploratory_only() -> None:
    now = datetime.now(UTC)
    outcomes = [
        DecisionOutcome(
            decision_id=uuid4(),
            decision_created_at=now,
            symbol="BTC/USDT",
            signal="buy",
            confidence=0.85,
            status=DecisionOutcomeStatus.CLOSED,
            decision_hit=index % 2 == 0,
        )
        for index in range(30)
    ]

    buckets = LearningService()._confidence_buckets(outcomes)
    high_score_bucket = next(bucket for bucket in buckets if bucket.lower == 0.8)

    assert high_score_bucket.decisions == 30
    assert high_score_bucket.sample_sufficient is True
