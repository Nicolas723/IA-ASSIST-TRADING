"""Bounded background collector for read-only shadow scans."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from src.application.services.market_scan_service import (
    MarketScanService,
    ShadowEvaluationService,
)
from src.domain.market.entities import Timeframe
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.application.services.market_data_service import MarketDataService

logger = get_logger(__name__)


class ShadowScanCollector:
    """Collect 1h shadow snapshots and evaluate matured one-candle horizons only."""

    def __init__(
        self,
        market_service_factory: Callable[[AsyncSession], MarketDataService],
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        symbols: list[str],
        interval_seconds: int,
    ) -> None:
        if not 1 <= len(symbols) <= 5:
            raise ValueError("Shadow collector requires between one and five symbols.")
        if interval_seconds < Timeframe.H1.seconds:
            raise ValueError("Shadow collector interval cannot be shorter than one hour.")
        self._market_service_factory = market_service_factory
        self._session_factory = session_factory
        self._symbols = list(symbols)
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.is_running:
            self._task = asyncio.create_task(self._run(), name="shadow-scan-collector")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await self.collect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("shadow_scan_collection_failed")
            await asyncio.sleep(self._interval_seconds)

    async def collect_once(self) -> None:
        """Persist one bounded 1h scan, then evaluate matured 1-candle scans."""
        from src.infrastructure.persistence.repositories.shadow_scan_repository import (
            SqlShadowScanRepository,
        )

        async with self._session_factory() as session:
            repository = SqlShadowScanRepository(session)
            market_service = self._market_service_factory(session)
            scan = await MarketScanService(market_service).scan(
                symbols=self._symbols,
                timeframe="1h",
                lookback_candles=24,
            )
            await repository.save(
                scan,
                requested_symbols=self._symbols,
                lookback_candles=24,
            )
            # At most the immediately preceding snapshot is needed for this
            # one-hour horizon; avoid re-fetching a large historical universe.
            scans = await repository.list_recent(limit=2)
            evaluator = ShadowEvaluationService(market_service)
            for previous in scans:
                if previous.scan_id == scan.scan_id or previous.timeframe != "1h":
                    continue
                result = await evaluator.evaluate(previous, horizon_candles=1)
                if result.status == "complete":
                    await repository.save_evaluation(result)
