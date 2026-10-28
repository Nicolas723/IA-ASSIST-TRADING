"""Durable engine log and the per-market decision audit filter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.application.services.autonomous_engine import AutonomousEngine, AutonomousLogEntry
from src.domain.agents.entities import CoordinatorDecision, SignalDirection
from src.infrastructure.persistence.database import Base
from src.infrastructure.persistence.repositories.engine_log_repository import (
    SqlEngineLogRepository,
)
from src.interfaces.api.v1.audit import get_decisions
from src.interfaces.api.v1.autonomous import get_autonomous_logs


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session
    await engine.dispose()


def _entry(minutes_ago: int, key: str = "decision.standby") -> AutonomousLogEntry:
    return AutonomousLogEntry(
        cycle=1,
        symbol="XRP/USDT",
        message="Standing by.",
        key=key,
        params={"score": 47.0},
        timestamp=datetime.now(tz=UTC) - timedelta(minutes=minutes_ago),
    )


async def test_repository_round_trip_pages_and_prunes(session) -> None:
    repo = SqlEngineLogRepository(session)
    old, middle, new = _entry(60 * 24 * 40), _entry(30), _entry(1, key="order.error")
    await repo.add_many([e.model_dump() for e in (old, middle, new)])

    rows = await repo.list_recent(limit=10)
    assert [AutonomousLogEntry.model_validate(r).id for r in rows] == [new.id, middle.id, old.id]
    assert rows[0]["key"] == "order.error"
    assert rows[1]["params"] == {"score": 47.0}

    paged = await repo.list_recent(limit=10, before=datetime.now(tz=UTC) - timedelta(minutes=5))
    assert [r["id"] for r in paged] == [middle.id, old.id]

    await repo.prune(datetime.now(tz=UTC) - timedelta(days=30))
    assert [r["id"] for r in await repo.list_recent()] == [new.id, middle.id]


async def test_engine_flushes_pending_logs_and_retries_after_a_failure() -> None:
    sink = AsyncMock(side_effect=[RuntimeError("db down"), None])
    engine = AutonomousEngine(log_sink=sink)
    engine.add_log(level="INFO", message="first")

    await engine.flush_logs()  # fails: the entry stays queued
    engine.add_log(level="INFO", message="second")
    await engine.flush_logs()

    assert [e.message for e in sink.call_args_list[1].args[0]] == ["first", "second"]
    await engine.flush_logs()
    assert sink.await_count == 2  # nothing left to write


async def test_logs_endpoint_merges_memory_and_database() -> None:
    engine = AutonomousEngine()
    engine.add_log(level="INFO", message="unsaved, newest")
    stored = _entry(90)
    repository = MagicMock()
    repository.list_recent = AsyncMock(return_value=[stored.model_dump()])

    logs = await get_autonomous_logs(engine=engine, repository=repository, limit=50, before=None)

    assert [log.message for log in logs] == ["unsaved, newest", "Standing by."]


async def test_decision_fallback_honours_the_symbol_filter() -> None:
    repository = MagicMock()
    repository.list_decisions = AsyncMock(return_value=[])
    agent_service = MagicMock()
    agent_service.get_audit_history.return_value = [
        CoordinatorDecision(
            symbol="XRP/USDT",
            final_signal=SignalDirection.BUY,
            aggregate_confidence=0.72,
            consensus_level=1.0,
            reasoning_summary="In-memory decision that belongs to another market entirely.",
            risk_approved=True,
        )
    ]

    btc = await get_decisions(repository, agent_service, limit=20, symbol="BTC/USDT")
    xrp = await get_decisions(repository, agent_service, limit=20, symbol="XRP-USDT")

    assert btc == []
    assert [d.symbol for d in xrp] == ["XRP/USDT"]
