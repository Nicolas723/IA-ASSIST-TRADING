"""
Market Data ORM Models for PostgreSQL / TimescaleDB.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DECIMAL, BigInteger, Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class CandleModel(Base):
    """
    SQLAlchemy model for a Candlestick (OHLCV).
    This maps to a TimescaleDB hypertable partitioned by time.
    """

    __tablename__ = "candles"

    # Primary key must include the partitioning column (time) in TimescaleDB
    # We use a composite primary key.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(30), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(5), primary_key=True)

    open: Mapped[float] = mapped_column(DECIMAL(20, 8))
    high: Mapped[float] = mapped_column(DECIMAL(20, 8))
    low: Mapped[float] = mapped_column(DECIMAL(20, 8))
    close: Mapped[float] = mapped_column(DECIMAL(20, 8))
    volume: Mapped[float] = mapped_column(DECIMAL(30, 8))

    is_closed: Mapped[bool] = mapped_column(Boolean, default=True)
    trades_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (Index("ix_candles_symbol_timeframe", "symbol", "timeframe"),)


class TickModel(Base):
    """
    SQLAlchemy model for real-time Ticks (trades).
    Maps to a TimescaleDB hypertable partitioned by time.
    """

    __tablename__ = "ticks"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(30), primary_key=True)
    # Using a sequence or just time+symbol+exchange+microsecond as PK
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    price: Mapped[float] = mapped_column(DECIMAL(20, 8))
    volume: Mapped[float] = mapped_column(DECIMAL(30, 8))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)  # buy/sell
