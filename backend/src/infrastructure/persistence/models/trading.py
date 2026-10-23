"""
Trading ORM Models for PostgreSQL / TimescaleDB.

Persists the trading domain entities so that orders, trades, positions and
portfolio snapshots survive process restarts (they used to live only in memory).

Design notes:
- Enums are stored as short strings (same convention as ``models/market.py``).
- Financial values use NUMERIC with explicit precision (never float).
- ``positions`` holds the CURRENT open positions (upsert on id, deleted on close).
- ``portfolio_snapshots`` is an append-only time series for equity tracking.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DECIMAL,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class OrderModel(Base):
    """A trading order (paper/backtest/real)."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)

    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(30, 8), nullable=False)

    limit_price: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    filled_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(30, 8), nullable=False, default=Decimal("0")
    )
    average_fill_price: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    commission: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )
    commission_asset: Mapped[str] = mapped_column(String(10), nullable=False, default="USDT")

    stop_loss: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)

    strategy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    signal_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    coordinator_decision_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        Index("ix_orders_symbol_status", "symbol", "status"),
        Index("ix_orders_mode_created", "mode", "created_at"),
    )


class TradeModel(Base):
    """A completed execution (filled order)."""

    __tablename__ = "trades"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)

    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(30, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(DECIMAL(20, 8), nullable=False)
    commission: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )
    commission_asset: Mapped[str] = mapped_column(String(10), nullable=False, default="USDT")

    strategy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_trades_symbol_executed", "symbol", "executed_at"),
        Index("ix_trades_order_id", "order_id"),
    )


class PositionModel(Base):
    """A currently-open position (upserted on id; deleted when closed)."""

    __tablename__ = "positions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)

    entry_price: Mapped[Decimal] = mapped_column(DECIMAL(20, 8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(30, 8), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )

    realized_pnl: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )
    total_commission: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )

    stop_loss: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 8), nullable=True)

    strategy_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_positions_symbol_mode", "symbol", "mode"),)


class PortfolioSnapshotModel(Base):
    """Append-only equity/balance snapshot for portfolio tracking."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)

    total_balance: Mapped[Decimal] = mapped_column(DECIMAL(20, 8), nullable=False)
    available_balance: Mapped[Decimal] = mapped_column(DECIMAL(20, 8), nullable=False)
    locked_balance: Mapped[Decimal] = mapped_column(DECIMAL(20, 8), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USDT")

    total_realized_pnl: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )
    daily_pnl: Mapped[Decimal] = mapped_column(DECIMAL(20, 8), nullable=False, default=Decimal("0"))
    peak_value: Mapped[Decimal] = mapped_column(
        DECIMAL(20, 8), nullable=False, default=Decimal("0")
    )
    position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_portfolio_snapshots_mode_updated", "mode", "updated_at"),)
