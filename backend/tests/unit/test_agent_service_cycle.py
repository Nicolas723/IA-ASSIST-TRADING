"""Every analysis run starts a fresh per-cycle LLM budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    def __init__(self) -> None:
        self.llm = FakeGateway()
        self.tasks: list[Any] = []

    async def coordinate(self, task: Any) -> CoordinatorDecision:
        self.tasks.append(task)
        return CoordinatorDecision(
            symbol=task.symbol,
            final_signal=SignalDirection.NEUTRAL,
            aggregate_confidence=0.3,
            consensus_level=1.0,
            reasoning_summary=(
                "Deterministic stub decision used to verify the per-cycle LLM "
                "budget is reset for every analysis run."
            ),
        )


class FakeMarket:
    async def get_candles(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
    ) -> list[Candle]:
        return [
            Candle(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                timeframe=timeframe,
                timestamp=datetime.now(tz=UTC) - timedelta(minutes=5),
                ohlcv=OHLCV(
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("1"),
                ),
            )
        ]


@pytest.mark.asyncio
async def test_run_analysis_resets_the_cycle_budget() -> None:
    coordinator = FakeCoordinator()
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    decision = await service.run_analysis("BTC/USDT", timeframe="1h")

    assert decision.final_signal is SignalDirection.NEUTRAL
    assert coordinator.llm.cycles == 1


@pytest.mark.asyncio
async def test_manual_analyses_do_not_share_the_cycle_budget() -> None:
    coordinator = FakeCoordinator()
    service = AgentService(coordinator=coordinator, market_service=FakeMarket())

    # The manual /agents/analyze endpoint calls run_analysis directly; each
    # call must get its own cycle budget instead of exhausting the first one.
    await service.run_analysis("BTC/USDT", timeframe="1h")
    await service.run_analysis("BTC/USDT", timeframe="1h")

    assert coordinator.llm.cycles == 2
    assert len(coordinator.tasks) == 2
