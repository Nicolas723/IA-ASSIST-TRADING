"""Batched committee analyses: one budget, sequential or parallel markets."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.application.services.agent_service import AgentService
from src.domain.agents.entities import CoordinatorDecision, SignalDirection
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe


class FakeGateway:
    def __init__(self) -> None:
        self.cycles = 0

    def begin_cycle(self) -> None:
        self.cycles += 1


class FakeCoordinator:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.llm = FakeGateway()
        self.tasks: list[Any] = []
        self.active = 0
        self.max_active = 0
        self.failing = failing or set()

    async def coordinate(self, task: Any) -> CoordinatorDecision:
        self.tasks.append(task)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            # Yield control so parallel calls genuinely overlap.
            await asyncio.sleep(0.01)
            if task.symbol in self.failing:
                raise RuntimeError(f"provider failed for {task.symbol}")
            return CoordinatorDecision(
                symbol=task.symbol,
                final_signal=SignalDirection.NEUTRAL,
                aggregate_confidence=0.3,
                consensus_level=1.0,
                reasoning_summary="Deterministic stub decision for the batch analysis tests.",
            )
        finally:
            self.active -= 1


class FakeMarket:
    def __init__(self, unavailable: set[str] | None = None) -> None:
        self.unavailable = unavailable or set()
        self.calls: list[str] = []

    async def get_candles(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
    ) -> list[Candle]:
        self.calls.append(symbol)
        if symbol in self.unavailable:
            raise RuntimeError(f"no candles for {symbol}")
        return [
            Candle(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                timeframe=timeframe,
                timestamp=datetime.now(tz=UTC),
                ohlcv=OHLCV(
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("1"),
                ),
                is_closed=True,
            )
        ]


@pytest.mark.asyncio
async def test_batch_opens_one_cycle_budget_for_all_markets() -> None:
    coordinator = FakeCoordinator()
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    results = await service.run_analyses(["BTC/USDT", "ETH/USDT", "SOL/USDT"])

    assert [symbol for symbol, _decision in results] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert coordinator.llm.cycles == 1
    assert coordinator.max_active == 1


@pytest.mark.asyncio
async def test_parallel_batch_runs_committee_calls_concurrently() -> None:
    coordinator = FakeCoordinator()
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    results = await service.run_analyses(
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        parallel=True,
    )

    assert len(results) == 3
    assert coordinator.llm.cycles == 1
    assert coordinator.max_active == 3


@pytest.mark.asyncio
async def test_unavailable_market_does_not_cancel_the_others() -> None:
    coordinator = FakeCoordinator()
    service = AgentService(
        coordinator=coordinator,
        market_service=FakeMarket(unavailable={"ETH/USDT"}),
    )

    results = await service.run_analyses(["BTC/USDT", "ETH/USDT", "SOL/USDT"], parallel=True)

    assert [symbol for symbol, _decision in results] == ["BTC/USDT", "SOL/USDT"]


@pytest.mark.asyncio
async def test_provider_failure_is_isolated_in_parallel_mode() -> None:
    coordinator = FakeCoordinator(failing={"ETH/USDT"})
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    results = await service.run_analyses(["BTC/USDT", "ETH/USDT", "SOL/USDT"], parallel=True)

    assert [symbol for symbol, _decision in results] == ["BTC/USDT", "SOL/USDT"]


@pytest.mark.asyncio
async def test_provider_failure_is_isolated_in_sequential_mode() -> None:
    coordinator = FakeCoordinator(failing={"BTC/USDT"})
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    results = await service.run_analyses(["BTC/USDT", "ETH/USDT"])

    assert [symbol for symbol, _decision in results] == ["ETH/USDT"]


@pytest.mark.asyncio
async def test_duplicate_and_unnormalized_symbols_are_deduplicated() -> None:
    coordinator = FakeCoordinator()
    market = FakeMarket()
    service = AgentService(coordinator=coordinator, market_service=market)

    results = await service.run_analyses(["btc-usdt", "BTC/USDT", " eth_usdt "])

    assert [symbol for symbol, _decision in results] == ["BTC/USDT", "ETH/USDT"]
    assert market.calls == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.asyncio
async def test_empty_batch_does_not_open_a_cycle_budget() -> None:
    coordinator = FakeCoordinator()
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    assert await service.run_analyses(["  "]) == []
    assert coordinator.llm.cycles == 0
