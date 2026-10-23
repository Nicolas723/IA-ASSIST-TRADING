"""
Audit ORM Model for PostgreSQL / TimescaleDB.

Persists the full AI Multi-Agent Committee decision trail so the "total
auditability" promise holds across restarts: every decision, its reasoning,
per-agent outputs, confidence and risk assessment are recoverable.

Design notes:
- Explicit ``Queryable`` columns are duplicated from the payload so the audit
  log can be filtered/paginated in SQL without deserialising JSON.
- ``payload`` holds the complete ``CoordinatorDecision`` as JSON and is the
  authoritative source when reconstructing the domain entity.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DECIMAL,
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class DecisionLogModel(Base):
    """A persisted :class:`CoordinatorDecision` (AI audit trail)."""

    __tablename__ = "decision_logs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)

    # Denormalised for fast querying / dashboards
    final_signal: Mapped[str] = mapped_column(String(20), nullable=False)
    aggregate_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    consensus_level: Mapped[float] = mapped_column(Float, nullable=False)
    risk_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Consolidated price targets (flattened; also inside payload)
    recommended_entry: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    recommended_stop: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    recommended_target: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)

    # Human-readable summary + counters
    reasoning_summary: Mapped[str] = mapped_column(Text, nullable=False)
    action_recommendation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Full lossless snapshot of the decision
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analysis_duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_decision_logs_symbol_created", "symbol", "created_at"),
        Index("ix_decision_logs_signal_created", "final_signal", "created_at"),
    )
