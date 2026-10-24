"""Repository for persisted shadow market scans."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.application.services.market_scan_service import ShadowEvaluationResult, ShadowScanResult
from src.infrastructure.persistence.models.shadow_scan import ShadowScanRunModel

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlShadowScanRepository:
    """Stores and lists immutable scan snapshots; never touches trading data."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        result: ShadowScanResult,
        requested_symbols: list[str],
        lookback_candles: int,
    ) -> None:
        model = ShadowScanRunModel(
            id=result.scan_id,
            timeframe=result.timeframe,
            lookback_candles=lookback_candles,
            requested_symbols=requested_symbols,
            payload=result.model_dump(mode="json"),
            scanned_at=result.scanned_at,
        )
        self._session.add(model)
        await self._session.commit()

    async def list_recent(self, limit: int = 50) -> list[ShadowScanResult]:
        statement = (
            select(ShadowScanRunModel).order_by(ShadowScanRunModel.scanned_at.desc()).limit(limit)
        )
        rows = await self._session.execute(statement)
        return [ShadowScanResult.model_validate(model.payload) for model in rows.scalars().all()]

    async def get(self, scan_id: UUID) -> ShadowScanResult | None:
        model = await self._session.get(ShadowScanRunModel, scan_id)
        return ShadowScanResult.model_validate(model.payload) if model else None

    async def save_evaluation(self, result: ShadowEvaluationResult) -> None:
        model = await self._session.get(ShadowScanRunModel, result.scan_id)
        if model is None:
            raise ValueError("Shadow scan not found.")
        if result.status == "complete":
            evaluations = dict(model.evaluations or {})
            evaluations[str(result.horizon_candles)] = result.model_dump(mode="json")
            model.evaluations = evaluations
            await self._session.commit()

    async def list_evaluations(self, limit: int = 1000) -> list[ShadowEvaluationResult]:
        rows = await self._session.execute(
            select(ShadowScanRunModel).order_by(ShadowScanRunModel.scanned_at.desc()).limit(limit)
        )
        results: list[ShadowEvaluationResult] = []
        for model in rows.scalars().all():
            results.extend(
                _evaluation_from_payload(item, model) for item in (model.evaluations or {}).values()
            )
        return results

    async def get_evaluations(self, scan_id: UUID) -> list[ShadowEvaluationResult] | None:
        model = await self._session.get(ShadowScanRunModel, scan_id)
        if model is None:
            return None
        return [
            _evaluation_from_payload(item, model) for item in (model.evaluations or {}).values()
        ]


def _evaluation_from_payload(
    payload: dict[str, object], model: ShadowScanRunModel
) -> ShadowEvaluationResult:
    """Backfill scan metadata for evaluations persisted before metadata was embedded."""
    result = ShadowEvaluationResult.model_validate(payload)
    updates: dict[str, object] = {}
    if result.scan_scanned_at is None:
        scanned_at = model.scanned_at
        updates["scan_scanned_at"] = (
            scanned_at.replace(tzinfo=UTC) if scanned_at.tzinfo is None else scanned_at
        )
    if result.timeframe is None:
        updates["timeframe"] = model.timeframe
    return result.model_copy(update=updates) if updates else result
