"""API tests for the MLOps decision-attribution endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.domain.agents.entities import CoordinatorDecision, SignalDirection
from src.domain.market.entities import Exchange
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.interfaces.api.v1 import mlops
from src.interfaces.api.v1.deps import get_decision_repository, get_trading_repository


def _decision() -> CoordinatorDecision:
    return CoordinatorDecision(
        id=uuid4(),
        symbol="BTC/USDT",
        final_signal=SignalDirection.BUY,
        aggregate_confidence=0.8,
        reasoning_summary=(
            "Synthetic decision used to verify the MLOps attribution endpoint "
            "and its response contract."
        ),
        consensus_level=0.9,
        risk_approved=True,
    )


def _order(decision_id: UUID, side: OrderSide, price: str) -> Order:
    now = datetime.now(UTC)
    qty = Decimal("1")
    return Order(
        id=uuid4(),
        symbol="BTC/USDT",
        exchange=Exchange.PAPER,
        mode=ExecutionMode.PAPER,
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
        filled_quantity=qty,
        average_fill_price=Decimal(price),
        commission=Decimal("0"),
        commission_asset="USDT",
        status=OrderStatus.FILLED,
        coordinator_decision_id=decision_id,
        created_at=now,
        filled_at=now,
    )


class _FakeDecisionRepository:
    def __init__(self, decisions: list[CoordinatorDecision]) -> None:
        self.decisions = decisions

    async def list_decisions(self, **_kwargs) -> list[CoordinatorDecision]:
        return self.decisions

    async def get_decision(self, decision_id: UUID) -> CoordinatorDecision | None:
        return next((item for item in self.decisions if item.id == decision_id), None)


class _FakeTradingRepository:
    def __init__(self, orders: list[Order]) -> None:
        self.orders = orders

    async def list_orders_for_attribution(self, **_kwargs) -> list[Order]:
        return self.orders


def _client(decisions: list[CoordinatorDecision], orders: list[Order]) -> AsyncClient:
    app = FastAPI()
    app.include_router(mlops.router, prefix="/api/v1/mlops")
    app.dependency_overrides[get_decision_repository] = lambda: _FakeDecisionRepository(decisions)
    app.dependency_overrides[get_trading_repository] = lambda: _FakeTradingRepository(orders)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_performance_endpoint_returns_attributed_summary() -> None:
    decision = _decision()
    orders = [
        _order(decision.id, OrderSide.BUY, "100"),
        _order(decision.id, OrderSide.SELL, "120"),
    ]

    async with _client([decision], orders) as client:
        response = await client.get("/api/v1/mlops/performance")

    assert response.status_code == 200
    body = response.json()
    assert body["total_decisions"] == 1
    assert body["resolved"] == 1
    assert body["win_rate_pct"] == 100.0
    assert float(body["net_pnl"]) == 20.0
    assert body["data_available"] is True


async def test_decision_outcome_endpoint_returns_single_result() -> None:
    decision = _decision()
    orders = [
        _order(decision.id, OrderSide.BUY, "100"),
        _order(decision.id, OrderSide.SELL, "90"),
    ]

    async with _client([decision], orders) as client:
        response = await client.get(f"/api/v1/mlops/decisions/{decision.id}/outcome")

    assert response.status_code == 200
    body = response.json()
    assert body["decision_id"] == str(decision.id)
    assert body["status"] == "closed"
    assert body["outcome"] == "loss"
    assert float(body["net_pnl"]) == -10.0


async def test_decision_outcome_returns_404_for_unknown_id() -> None:
    async with _client([], []) as client:
        response = await client.get(f"/api/v1/mlops/decisions/{uuid4()}/outcome")

    assert response.status_code == 404


def _closed_round_trip(index: int) -> tuple[CoordinatorDecision, list[Order]]:
    decision = _decision()
    closed_at = datetime(2026, 10, 24, 12, 0, tzinfo=UTC) + timedelta(minutes=index)
    qty = Decimal("1")
    orders = [
        Order(
            id=uuid4(),
            symbol="BTC/USDT",
            exchange=Exchange.PAPER,
            mode=ExecutionMode.PAPER,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            filled_quantity=qty,
            average_fill_price=Decimal(price),
            commission=Decimal("0"),
            commission_asset="USDT",
            status=OrderStatus.FILLED,
            coordinator_decision_id=decision.id,
            created_at=closed_at,
            filled_at=closed_at,
        )
        for side, price in ((OrderSide.BUY, "100"), (OrderSide.SELL, "110"))
    ]
    return decision, orders


async def test_drift_endpoint_returns_windowed_report() -> None:
    decisions: list[CoordinatorDecision] = []
    orders: list[Order] = []
    for index in range(30):
        decision, round_trip = _closed_round_trip(index)
        decisions.append(decision)
        orders.extend(round_trip)

    async with _client(decisions, orders) as client:
        response = await client.get(
            "/api/v1/mlops/drift",
            params={"recent_window": 10, "baseline_window": 20},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stable"
    assert body["recent"]["sample_size"] == 10
    assert body["baseline"]["sample_size"] == 20
    assert body["enforcement"] == "read_only_no_auto_suspension"


async def test_drift_endpoint_reports_insufficient_data_without_history() -> None:
    async with _client([], []) as client:
        response = await client.get("/api/v1/mlops/drift")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["recent"]["sample_size"] == 0
