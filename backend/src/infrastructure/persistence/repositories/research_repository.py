"""Persistence for versioned research notes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.domain.research.entities import (
    ResearchKind,
    ResearchNote,
    ResearchNoteCreate,
    ValidationStatus,
)
from src.infrastructure.persistence.models.research import ResearchNoteModel

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_symbol(value: str) -> str:
    """Canonicalise ``BTC/USDT``, ``btc-usdt`` and ``BTCUSDT`` to one key."""
    return "".join(character for character in value.upper() if character.isalnum())


class SqlResearchRepository:
    """Append-only revisions of research notes; never touches trading state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, note: ResearchNote) -> ResearchNote:
        await self._session.merge(self._to_model(note))
        await self._session.commit()
        return note

    async def get(self, note_id: UUID) -> ResearchNote | None:
        model = await self._session.get(ResearchNoteModel, note_id)
        return self._to_domain(model) if model is not None else None

    async def list_notes(
        self,
        *,
        kind: ResearchKind | None = None,
        status: ValidationStatus | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[ResearchNote]:
        statement = select(ResearchNoteModel)
        if kind is not None:
            statement = statement.where(ResearchNoteModel.kind == kind.value)
        if status is not None:
            statement = statement.where(ResearchNoteModel.validation_status == status.value)
        statement = statement.order_by(ResearchNoteModel.created_at.desc()).limit(limit)
        rows = await self._session.execute(statement)
        notes = [self._to_domain(model) for model in rows.scalars().all()]
        if symbol is not None:
            wanted = _normalize_symbol(symbol)
            notes = [
                note
                for note in notes
                if any(_normalize_symbol(stored) == wanted for stored in note.symbols)
            ]
        return notes

    async def supersede(
        self, note_id: UUID, replacement: ResearchNoteCreate
    ) -> ResearchNote | None:
        """Create a new revision and mark the previous one superseded."""
        previous = await self._session.get(ResearchNoteModel, note_id)
        if previous is None:
            return None
        note = replacement.to_note()
        note.version = previous.version + 1
        note.supersedes_id = previous.id
        previous.validation_status = ValidationStatus.SUPERSEDED.value
        await self._session.merge(previous)
        self._session.add(self._to_model(note))
        await self._session.commit()
        return note

    async def list_context(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 20,
    ) -> list[ResearchNote]:
        """Current notes relevant to a symbol/timeframe, newest first.

        Global notes (no symbols) are included because they may describe method
        or data-quality constraints. This is context for humans or prompts; it
        never overrides a signal or risk decision.
        """
        notes = await self.list_notes(limit=500)
        wanted = _normalize_symbol(symbol) if symbol else None
        selected: list[ResearchNote] = []
        for note in notes:
            if not note.is_current():
                continue
            if wanted is not None and note.symbols:
                matches_symbol = any(_normalize_symbol(stored) == wanted for stored in note.symbols)
                if not matches_symbol:
                    continue
            if timeframe is not None and note.timeframe is not None and note.timeframe != timeframe:
                continue
            selected.append(note)
        return selected[:limit]

    async def update_status(self, note_id: UUID, status: ValidationStatus) -> ResearchNote | None:
        """Change the validation state of a current note."""
        if status == ValidationStatus.SUPERSEDED:
            raise ValueError("Use the supersede endpoint to replace a note.")
        model = await self._session.get(ResearchNoteModel, note_id)
        if model is None:
            return None
        if model.validation_status == ValidationStatus.SUPERSEDED.value:
            raise ValueError("Superseded notes are immutable; create a new revision.")
        model.validation_status = status.value
        await self._session.commit()
        return self._to_domain(model)

    @staticmethod
    def _to_model(note: ResearchNote) -> ResearchNoteModel:
        return ResearchNoteModel(
            id=note.id,
            kind=note.kind.value,
            title=note.title,
            statement=note.statement,
            symbols=note.symbols,
            timeframe=note.timeframe,
            sources=note.sources,
            created_by=note.created_by,
            evidence=note.evidence,
            validation_status=note.validation_status.value,
            version=note.version,
            supersedes_id=note.supersedes_id,
            valid_from=_ensure_utc(note.valid_from),
            expires_at=_ensure_utc(note.expires_at) if note.expires_at else None,
            created_at=_ensure_utc(note.created_at),
        )

    @staticmethod
    def _to_domain(model: ResearchNoteModel) -> ResearchNote:
        return ResearchNote(
            id=model.id,
            kind=ResearchKind(model.kind),
            title=model.title,
            statement=model.statement,
            symbols=list(model.symbols or []),
            timeframe=model.timeframe,
            sources=list(model.sources or []),
            created_by=model.created_by,
            evidence=dict(model.evidence or {}),
            validation_status=ValidationStatus(model.validation_status),
            version=model.version,
            supersedes_id=model.supersedes_id,
            valid_from=_ensure_utc(model.valid_from),
            expires_at=_ensure_utc(model.expires_at) if model.expires_at else None,
            created_at=_ensure_utc(model.created_at),
        )
