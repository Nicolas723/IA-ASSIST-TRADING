"""SQLAlchemy store for autonomous-engine log entries.

Entries travel as plain dicts (the fields of ``AutonomousLogEntry``) so the
persistence layer does not depend on the application service that emits them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from src.infrastructure.persistence.models.audit import EngineLogModel
from src.infrastructure.persistence.repositories.trading_repository import _ensure_utc

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

_FIELDS = (
    "level",
    "cycle",
    "symbol",
    "key",
    "params",
    "message",
    "signal",
    "confidence_pct",
    "price",
    "order_id",
)


class SqlEngineLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, entries: list[dict[str, Any]]) -> None:
        self._session.add_all(
            EngineLogModel(
                entry_id=str(entry["id"]),
                timestamp=_ensure_utc(entry["timestamp"]),
                **{field: entry.get(field) for field in _FIELDS},
            )
            for entry in entries
        )
        await self._session.commit()

    async def list_recent(
        self, limit: int = 50, before: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Newest first; ``before`` pages further back in time."""
        stmt: Any = select(EngineLogModel)
        if before is not None:
            stmt = stmt.where(EngineLogModel.timestamp < _ensure_utc(before))
        stmt = stmt.order_by(EngineLogModel.timestamp.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "id": row.entry_id,
                "timestamp": row.timestamp,
                **{field: getattr(row, field) for field in _FIELDS},
            }
            for row in rows
        ]

    async def prune(self, older_than: datetime) -> None:
        await self._session.execute(
            delete(EngineLogModel).where(EngineLogModel.timestamp < _ensure_utc(older_than))
        )
        await self._session.commit()
