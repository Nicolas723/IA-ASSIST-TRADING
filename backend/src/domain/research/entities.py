"""
Versioned research memory.

Notes are append-only in spirit: corrections create a new version that points
at the note it supersedes. Nothing here influences trading automatically; the
purpose is durable provenance and an explicit validation state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class ResearchKind(StrEnum):
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    DECISION = "decision"


class ValidationStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ResearchNote(BaseModel):
    """A durable claim or finding with scope, provenance, and evidence."""

    id: UUID = Field(default_factory=uuid4)
    kind: ResearchKind
    title: str = Field(min_length=3, max_length=200)
    statement: str = Field(min_length=10)
    symbols: list[str] = Field(default_factory=list)
    timeframe: str | None = None
    sources: list[str] = Field(default_factory=list)
    created_by: str = Field(default="human", pattern="^(human|agent)$")
    evidence: dict[str, Any] = Field(default_factory=dict)
    validation_status: ValidationStatus = ValidationStatus.PROPOSED
    version: int = Field(default=1, ge=1)
    supersedes_id: UUID | None = None
    valid_from: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @model_validator(mode="after")
    def validate_provenance_and_dates(self) -> ResearchNote:
        if self.kind in (ResearchKind.OBSERVATION, ResearchKind.EXPERIMENT) and not self.sources:
            msg = "Observations and experiments require at least one source."
            raise ValueError(msg)
        if self.expires_at is not None and self.expires_at <= self.valid_from:
            msg = "expires_at must be after valid_from."
            raise ValueError(msg)
        return self

    def is_current(self, now: datetime | None = None) -> bool:
        """A note is current when it is not superseded, refuted, or expired."""
        if self.validation_status in (
            ValidationStatus.REFUTED,
            ValidationStatus.EXPIRED,
            ValidationStatus.SUPERSEDED,
        ):
            return False
        reference = now or datetime.now(tz=UTC)
        return self.expires_at is None or self.expires_at > reference


class ResearchNoteCreate(BaseModel):
    """Input contract for a new note; the caller never supplies ids/status."""

    kind: ResearchKind
    title: str = Field(min_length=3, max_length=200)
    statement: str = Field(min_length=10)
    symbols: list[str] = Field(default_factory=list)
    timeframe: str | None = None
    sources: list[str] = Field(default_factory=list)
    created_by: str = Field(default="human", pattern="^(human|agent)$")
    evidence: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    def to_note(self) -> ResearchNote:
        return ResearchNote(**self.model_dump())
