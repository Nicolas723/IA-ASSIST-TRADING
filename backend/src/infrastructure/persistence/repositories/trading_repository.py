"""
SQLAlchemy implementation of the Trading Repository.

Persists orders, trades, positions and portfolio snapshots so the trading
journal survives process restarts.

The repository commits per operation (matching ``TimescaleCandleRepository``)
and uses ``session.merge`` for upsert semantics so it stays dialect-agnostic
(PostgreSQL in production, SQLite in unit tests).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from src.domain.market.entities import Exchange, SymbolStr
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    Trade,
)
from src.domain.trading.ports import TradingRepositoryPort
from src.infrastructure.persistence.models.trading import (
    OrderModel,
    PortfolioSnapshotModel,
    PositionModel,
    TradeModel,
)
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _ensure_utc(value: datetime | None) -> datetime | None:
    """Coerce naive datetimes to UTC (the domain uses ``datetime.utcnow()``)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _dec(value: Decimal | float | int | None) -> Decimal | None:
    """Coerce a nullable DB numeric back to Decimal without float precision loss."""
    if value is None:
        return None
    return _dec_required(value)


def _dec_required(value: Decimal | float | int) -> Decimal:
    """Coerce a non-null DB numeric back to Decimal."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# -- Order ------------------------------------------------------------------


def order_to_model(order: Order) -> OrderModel:
    return OrderModel(
        id=order.id,
        symbol=order.symbol,
        exchange=order.exchange.value,
        mode=order.mode.value,
        side=order.side.value,
        order_type=order.order_type.value,
        quantity=order.quantity,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        status=order.status.value,
        filled_quantity=order.filled_quantity,
        average_fill_price=order.average_fill_price,
        commission=order.commission,
        commission_asset=order.commission_asset,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        strategy_id=order.strategy_id,
        signal_id=order.signal_id,
        coordinator_decision_id=order.coordinator_decision_id,
        client_order_id=order.client_order_id,
        created_at=_ensure_utc(order.created_at),
        submitted_at=_ensure_utc(order.submitted_at),
        filled_at=_ensure_utc(order.filled_at),
        cancelled_at=_ensure_utc(order.cancelled_at),
        rejection_reason=order.rejection_reason,
        notes=order.notes,
    )


def model_to_order(model: OrderModel) -> Order:
    return Order(
        id=model.id,
        symbol=model.symbol,
        exchange=Exchange(model.exchange),
        mode=ExecutionMode(model.mode),
        side=OrderSide(model.side),
        order_type=OrderType(model.order_type),
        quantity=_dec_required(model.quantity),
        limit_price=_dec(model.limit_price),
        stop_price=_dec(model.stop_price),
        status=OrderStatus(model.status),
        filled_quantity=_dec_required(model.filled_quantity),
        average_fill_price=_dec(model.average_fill_price),
        commission=_dec_required(model.commission),
        commission_asset=model.commission_asset,
        stop_loss=_dec(model.stop_loss),
        take_profit=_dec(model.take_profit),
        strategy_id=model.strategy_id,
        signal_id=model.signal_id,
        coordinator_decision_id=model.coordinator_decision_id,
        client_order_id=model.client_order_id,
        created_at=model.created_at,
        submitted_at=model.submitted_at,
        filled_at=model.filled_at,
        cancelled_at=model.cancelled_at,
        rejection_reason=model.rejection_reason,
        notes=model.notes,
    )


# -- Trade ------------------------------------------------------------------


def trade_to_model(trade: Trade) -> TradeModel:
    return TradeModel(
        id=trade.id,
        order_id=trade.order_id,
        symbol=trade.symbol,
        exchange=trade.exchange.value,
        mode=trade.mode.value,
        side=trade.side.value,
        quantity=trade.quantity,
        price=trade.price,
        commission=trade.commission,
        commission_asset=trade.commission_asset,
        strategy_id=trade.strategy_id,
        executed_at=_ensure_utc(trade.executed_at),
    )


def model_to_trade(model: TradeModel) -> Trade:
    return Trade(
        id=model.id,
        order_id=model.order_id,
        symbol=model.symbol,
        exchange=Exchange(model.exchange),
        mode=ExecutionMode(model.mode),
        side=OrderSide(model.side),
        quantity=_dec_required(model.quantity),
        price=_dec_required(model.price),
        commission=_dec_required(model.commission),
        commission_asset=model.commission_asset,
        strategy_id=model.strategy_id,
        executed_at=model.executed_at,
    )


# -- Position ---------------------------------------------------------------


def position_to_model(position: Position) -> PositionModel:
    return PositionModel(
        id=position.id,
        symbol=position.symbol,
        exchange=position.exchange.value,
        mode=position.mode.value,
        side=position.side.value,
        entry_price=position.entry_price,
        quantity=position.quantity,
        current_price=position.current_price,
        realized_pnl=position.realized_pnl,
        total_commission=position.total_commission,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        strategy_id=position.strategy_id,
        opened_at=_ensure_utc(position.opened_at),
        updated_at=_ensure_utc(position.updated_at),
    )


def model_to_position(model: PositionModel) -> Position:
    return Position(
        id=model.id,
        symbol=model.symbol,
        exchange=Exchange(model.exchange),
        mode=ExecutionMode(model.mode),
        side=PositionSide(model.side),
        entry_price=_dec_required(model.entry_price),
        quantity=_dec_required(model.quantity),
        current_price=_dec_required(model.current_price),
        realized_pnl=_dec_required(model.realized_pnl),
        total_commission=_dec_required(model.total_commission),
        stop_loss=_dec(model.stop_loss),
        take_profit=_dec(model.take_profit),
        strategy_id=model.strategy_id,
        opened_at=model.opened_at,
        updated_at=model.updated_at,
    )


# -- Portfolio --------------------------------------------------------------


class SqlTradingRepository(TradingRepositoryPort):
    """SQLAlchemy-backed trading journal."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_order(self, order: Order) -> None:
        await self._session.merge(order_to_model(order))
        await self._session.commit()

    async def get_order(self, order_id: UUID) -> Order | None:
        model = await self._session.get(OrderModel, order_id)
        return model_to_order(model) if model else None

    async def list_orders(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
        limit: int = 100,
    ) -> list[Order]:
        stmt = select(OrderModel)
        if mode is not None:
            stmt = stmt.where(OrderModel.mode == mode.value)
        if symbol is not None:
            stmt = stmt.where(OrderModel.symbol == symbol)
        stmt = stmt.order_by(OrderModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [model_to_order(m) for m in result.scalars().all()]

    async def save_trade(self, trade: Trade) -> None:
        await self._session.merge(trade_to_model(trade))
        await self._session.commit()

    async def list_trades(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        stmt = select(TradeModel)
        if mode is not None:
            stmt = stmt.where(TradeModel.mode == mode.value)
        if symbol is not None:
            stmt = stmt.where(TradeModel.symbol == symbol)
        stmt = stmt.order_by(TradeModel.executed_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [model_to_trade(m) for m in result.scalars().all()]

    async def upsert_position(self, position: Position) -> None:
        await self._session.merge(position_to_model(position))
        await self._session.commit()

    async def delete_position(self, position_id: UUID) -> None:
        await self._session.execute(delete(PositionModel).where(PositionModel.id == position_id))
        await self._session.commit()

    async def list_positions(
        self,
        mode: ExecutionMode | None = None,
        symbol: SymbolStr | None = None,
    ) -> list[Position]:
        stmt = select(PositionModel)
        if mode is not None:
            stmt = stmt.where(PositionModel.mode == mode.value)
        if symbol is not None:
            stmt = stmt.where(PositionModel.symbol == symbol)
        stmt = stmt.order_by(PositionModel.opened_at.desc())
        result = await self._session.execute(stmt)
        return [model_to_position(m) for m in result.scalars().all()]

    async def sync_positions(
        self,
        mode: ExecutionMode,
        positions: list[Position],
    ) -> None:
        current_ids = {position.id for position in positions}
        result = await self._session.execute(
            select(PositionModel.id).where(PositionModel.mode == mode.value)
        )
        stale_ids = set(result.scalars().all()) - current_ids
        if stale_ids:
            await self._session.execute(
                delete(PositionModel).where(PositionModel.id.in_(stale_ids))
            )
        for position in positions:
            await self._session.merge(position_to_model(position))
        await self._session.commit()

    async def save_portfolio_snapshot(self, portfolio: Portfolio, exchange: Exchange) -> None:
        model = PortfolioSnapshotModel(
            mode=portfolio.mode.value,
            exchange=exchange.value,
            total_balance=portfolio.total_balance,
            available_balance=portfolio.available_balance,
            locked_balance=portfolio.locked_balance,
            base_currency=portfolio.base_currency,
            total_realized_pnl=portfolio.total_realized_pnl,
            daily_pnl=portfolio.daily_pnl,
            peak_value=portfolio.peak_value,
            position_count=portfolio.position_count,
            updated_at=_ensure_utc(portfolio.updated_at),
        )
        self._session.add(model)
        await self._session.commit()

    async def latest_portfolio_snapshot(
        self, mode: ExecutionMode, exchange: Exchange
    ) -> Portfolio | None:
        stmt = (
            select(PortfolioSnapshotModel)
            .where(PortfolioSnapshotModel.mode == mode.value)
            .where(PortfolioSnapshotModel.exchange == exchange.value)
            .order_by(PortfolioSnapshotModel.updated_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return Portfolio(
            mode=ExecutionMode(model.mode),
            exchange=Exchange(model.exchange),
            total_balance=_dec_required(model.total_balance),
            available_balance=_dec_required(model.available_balance),
            locked_balance=_dec_required(model.locked_balance),
            base_currency=model.base_currency,
            total_realized_pnl=_dec_required(model.total_realized_pnl),
            daily_pnl=_dec_required(model.daily_pnl),
            peak_value=_dec_required(model.peak_value),
            updated_at=model.updated_at,
        )

    async def clear_journal(self, mode: ExecutionMode) -> None:
        mode_value = mode.value
        await self._session.execute(delete(OrderModel).where(OrderModel.mode == mode_value))
        await self._session.execute(delete(TradeModel).where(TradeModel.mode == mode_value))
        await self._session.execute(delete(PositionModel).where(PositionModel.mode == mode_value))
        await self._session.execute(
            delete(PortfolioSnapshotModel).where(PortfolioSnapshotModel.mode == mode_value)
        )
        await self._session.commit()
