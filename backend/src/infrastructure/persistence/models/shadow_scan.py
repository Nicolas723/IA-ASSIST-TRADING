"""Durable snapshots from read-only market screening runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class ShadowScanRunModel(Base):
    """One persisted shadow scan response for later evaluation and audit."""

    __tablename__ = "shadow_scan_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    lookback_candles: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evaluations: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_shadow_scan_runs_scanned_at", "scanned_at"),)
