"""The assessment endpoint is wired read-only and abstains without evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.application.services.market_scan_service import (
    ShadowScanCandidate,
    ShadowScanResult,
)
from src.infrastructure.persistence.database import Base
from src.infrastructure.persistence.repositories.shadow_scan_repository import (
    SqlShadowScanRepository,
)
from src.interfaces.api.v1.deps import get_shadow_scan_repository
from src.main import app


@pytest.fixture
async def scan_repo():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield SqlShadowScanRepository(session)
    await engine.dispose()


def make_scan(scanned_at: datetime) -> ShadowScanResult:
    return ShadowScanResult(
        scan_id=uuid4(),
        timeframe="1h",
        scanned_at=scanned_at,
        candidates=[
            ShadowScanCandidate(
                symbol="BTC/USDT",
                status="observed",
                rank=1,
                last_price=60_000,
                latest_candle_at=scanned_at,
                lookback_return_pct=0.8,
                activity_score=1.0,
                order_book_at=scanned_at,
                quoted_spread_pct=0.05,
                observed_movement_less_spread_pct=0.8,
                spread_status="observed",
            )
        ],
    )


async def test_assessment_endpoint_abstains_and_never_enables_execution(scan_repo) -> None:
    now = datetime.now(tz=UTC)
    scan = make_scan(now - timedelta(minutes=1))
    await scan_repo.save(scan, requested_symbols=["BTC/USDT"], lookback_candles=50)

    app.dependency_overrides[get_shadow_scan_repository] = lambda: scan_repo
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/agents/scan/shadow/{scan.scan_id}/assessment")
    finally:
        app.dependency_overrides.pop(get_shadow_scan_repository, None)

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "abstain"
    assert body["selected_symbol"] is None
    assert body["execution_enabled"] is False
    assert body["automatic_routing"] is False
    assert body["candidates"][0]["eligible"] is True
    assert body["evidence"]["matured_evaluations"] == 0


async def test_assessment_endpoint_returns_404_for_unknown_scan(scan_repo) -> None:
    app.dependency_overrides[get_shadow_scan_repository] = lambda: scan_repo
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/agents/scan/shadow/{uuid4()}/assessment")
    finally:
        app.dependency_overrides.pop(get_shadow_scan_repository, None)

    assert response.status_code == 404
