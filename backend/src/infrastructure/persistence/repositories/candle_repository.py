"""
TimescaleDB implementation for Candle Repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from src.domain.market.entities import OHLCV, Candle, Exchange, SymbolStr, Timeframe
from src.domain.market.ports import CandleRepositoryPort
from src.infrastructure.persistence.models.market import CandleModel
from src.shared.exceptions.domain import DatabaseError

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class TimescaleCandleRepository(CandleRepositoryPort):
    """
    Repository for persisting and querying candles in TimescaleDB.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_candles(self, candles: list[Candle]) -> None:
        if not candles:
            return

        try:
            from sqlalchemy.dialects.postgresql import insert

            values = [
                {
                    "time": c.timestamp,
                    "symbol": c.symbol,
                    "exchange": c.exchange.value,
                    "timeframe": c.timeframe.value,
                    "open": float(c.ohlcv.open),
                    "high": float(c.ohlcv.high),
                    "low": float(c.ohlcv.low),
                    "close": float(c.ohlcv.close),
                    "volume": float(c.ohlcv.volume),
                    "is_closed": c.is_closed,
                    "trades_count": getattr(c.ohlcv, "trades_count", None),
                }
                for c in candles
            ]
            stmt = insert(CandleModel).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["time", "symbol", "exchange", "timeframe"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "is_closed": stmt.excluded.is_closed,
                    "trades_count": stmt.excluded.trades_count,
                },
            )
            await self._session.execute(stmt)
            await self._session.commit()
        except Exception as e:
            await self._session.rollback()
            raise DatabaseError(f"Failed to save candles: {e}") from e

    async def get_candles(
        self,
        symbol: SymbolStr,
        exchange: Exchange,
        timeframe: Timeframe,
        start: datetime,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        try:
            stmt = select(CandleModel).where(
                CandleModel.symbol == symbol,
                CandleModel.exchange == exchange.value,
                CandleModel.timeframe == timeframe.value,
                CandleModel.time >= start,
            )

            if end:
                stmt = stmt.where(CandleModel.time <= end)

            stmt = stmt.order_by(CandleModel.time.asc())

            if limit:
                stmt = stmt.limit(limit)

            result = await self._session.execute(stmt)
            models = result.scalars().all()

            # Map back to domain entities
            from decimal import Decimal

            return [
                Candle(
                    symbol=symbol,
                    exchange=exchange,
                    timeframe=timeframe,
                    timestamp=m.time,
                    ohlcv=OHLCV(
                        open=Decimal(str(m.open)),
                        high=Decimal(str(m.high)),
                        low=Decimal(str(m.low)),
                        close=Decimal(str(m.close)),
                        volume=Decimal(str(m.volume)),
                    ),
                    is_closed=m.is_closed,
                )
                for m in models
            ]
        except Exception as e:
            raise DatabaseError(f"Failed to fetch candles: {e}") from e
