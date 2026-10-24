"""Research memory API: versioned notes with provenance and validation state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.domain.research.entities import (
    ResearchKind,
    ResearchNote,
    ResearchNoteCreate,
    ValidationStatus,
)
from src.infrastructure.persistence.repositories.research_repository import (
    SqlResearchRepository,
)
from src.interfaces.api.v1.deps import get_research_repository

router = APIRouter()


class StatusUpdateRequest(BaseModel):
    status: ValidationStatus


class ResearchContext(BaseModel):
    """Current notes relevant to a symbol/timeframe, for human or prompt context."""

    symbol: str | None = None
    timeframe: str | None = None
    generated_at: datetime
    notes: list[ResearchNote]
    usage_policy: str = "context_only_no_signal_override"
    disclaimer: str = (
        "Notes describe prior observations and hypotheses. They are not a signal, "
        "probability of profit, or authorisation to trade."
    )


@router.get(
    "/context",
    response_model=ResearchContext,
    summary="Current research context for a symbol and timeframe",
)
async def get_research_context(
    repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
) -> ResearchContext:
    """Read-only context. Consuming it never bypasses risk or the committee."""
    notes = await repository.list_context(symbol=symbol, timeframe=timeframe, limit=limit)
    return ResearchContext(
        symbol=symbol,
        timeframe=timeframe,
        generated_at=datetime.now(tz=UTC),
        notes=notes,
    )


@router.post(
    "/notes",
    response_model=ResearchNote,
    status_code=201,
    summary="Store a versioned research note",
)
async def create_research_note(
    payload: ResearchNoteCreate,
    repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
) -> ResearchNote:
    """Persist a note with its scope, sources, and evidence.

    Notes never influence trading automatically; they exist for audit and for
    later review of what was believed, when, and why.
    """
    try:
        return await repository.save(payload.to_note())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/notes", response_model=list[ResearchNote], summary="List research notes")
async def list_research_notes(
    repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
    kind: ResearchKind | None = None,
    status: ValidationStatus | None = None,
    symbol: str | None = None,
    current_only: bool = False,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ResearchNote]:
    notes = await repository.list_notes(kind=kind, status=status, symbol=symbol, limit=limit)
    return [note for note in notes if note.is_current()] if current_only else notes


@router.get("/notes/{note_id}", response_model=ResearchNote, summary="Get one research note")
async def get_research_note(
    note_id: UUID,
    repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
) -> ResearchNote:
    note = await repository.get(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Research note not found.")
    return note


@router.post(
    "/notes/{note_id}/supersede",
    response_model=ResearchNote,
    status_code=201,
    summary="Create a new revision of a research note",
)
async def supersede_research_note(
    note_id: UUID,
    payload: ResearchNoteCreate,
    repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
) -> ResearchNote:
    note = await repository.supersede(note_id, payload)
    if note is None:
        raise HTTPException(status_code=404, detail="Research note not found.")
    return note


@router.post(
    "/notes/{note_id}/status",
    response_model=ResearchNote,
    summary="Update the validation state of a current note",
)
async def update_research_note_status(
    note_id: UUID,
    payload: StatusUpdateRequest,
    repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
) -> ResearchNote:
    try:
        note = await repository.update_status(note_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if note is None:
        raise HTTPException(status_code=404, detail="Research note not found.")
    return note
