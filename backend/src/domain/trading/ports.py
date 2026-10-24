"""
Trading domain ports (interfaces).

Defines the persistence contract for trading entities (orders, trades,
positions, portfolio snapshots). Implemented by the SQLAlchemy repository
in the infrastructure layer.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from src.domain.market.entities import Exchange, SymbolStr
    from src.domain.trading.entities import (
        ExecutionMode,
        Order,
        Portfolio,
        Position,
        Trade,
    )


class TradingRepositoryPort(abc.ABC):
    """
    Interface for persisting the trading journal.

    The repository is a *durable append-only journal*: orders and trades are
    never mutated, while positions and portfolio snapshots reflect current
    state and are upserted as they change.
    """

    # -- Orders -------------------------------------------------------------

    @abc.abstractmethod
    async def save_order(self, order: Order) -> None:
        """Insert or update an order by id."""
        ...

    @abc.abstractmethod
    async def get_order(self, order_id: UUID) -> Order | None:
        """Retrieve a single order by id, or ``None`` if not found."""
        ...

    @abc.abstractmethod
    async def list_orders(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
        limit: int = 100,
    ) -> list[Order]:
        """List orders, newest first, optionally filtered."""
        ...

    @abc.abstractmethod
    async def list_orders_for_attribution(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
        since: datetime | None = None,
        decision_id: UUID | None = None,
        limit: int = 5000,
    ) -> list[Order]:
        """
        List filled orders for decision/outcome attribution.

        Unlike ``list_orders`` this is deliberately scoped to filled executions
        and supports filtering by the linked coordinator decision.
        """
        ...

    # -- Trades -------------------------------------------------------------

    @abc.abstractmethod
    async def save_trade(self, trade: Trade) -> None:
        """Insert a trade (idempotent by id)."""
        ...

    @abc.abstractmethod
    async def list_trades(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        """List trades, newest first, optionally filtered."""
        ...

    # -- Positions ----------------------------------------------------------

    @abc.abstractmethod
    async def upsert_position(self, position: Position) -> None:
        """Insert or update a currently-open position by id."""
        ...

    @abc.abstractmethod
    async def delete_position(self, position_id: UUID) -> None:
        """Remove a position (called when it is closed)."""
        ...

    @abc.abstractmethod
    async def sync_positions(
        self,
        mode: ExecutionMode,
        positions: list[Position],
    ) -> None:
        """
        Reconcile the persisted open positions with the given snapshot.

        Upserts every supplied position and deletes any persisted position for
        the mode that is no longer present (e.g. closed by the bot).
        """
        ...

    @abc.abstractmethod
    async def list_positions(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
    ) -> list[Position]:
        """List currently-open positions, optionally filtered."""
        ...

    # -- Portfolio snapshots ------------------------------------------------

    @abc.abstractmethod
    async def save_portfolio_snapshot(self, portfolio: Portfolio, exchange: Exchange) -> None:
        """Append an equity/balance snapshot for the portfolio."""
        ...

    @abc.abstractmethod
    async def latest_portfolio_snapshot(
        self, mode: ExecutionMode, exchange: Exchange
    ) -> Portfolio | None:
        """Return the most recent snapshot for a mode/exchange, if any."""
        ...

    @abc.abstractmethod
    async def clear_journal(self, mode: ExecutionMode) -> None:
        """
        Wipe all persisted trading state for a mode (orders, trades, positions
        and portfolio snapshots). Used when the paper account is reset.
        """
        ...
