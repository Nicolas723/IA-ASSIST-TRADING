"""
MarketDataService — Application Layer.

Orchestrates:
1. Fetching historical OHLCV from exchange adapters (CCXT)
2. Persisting to TimescaleDB via CandleRepository
3. Serving cached data back to API endpoints

Design:
- Stateless service; all dependencies injected
- DB upsert (ON CONFLICT DO NOTHING) for idempotent re-runs
- Structured logging on every significant action
- Returns domain entities, never ORM models
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from decimal import Decimal

    from src.domain.market.entities import Candle, OrderBook, Timeframe
    from src.domain.market.ports import CandleRepositoryPort, MarketDataPort

logger = get_logger(__name__)


class MarketDataService:
    """
    Coordinates market data ingestion and retrieval.

    Usage:
        service = MarketDataService(exchange_adapter, candle_repo)
        candles = await service.get_candles("BTC/USDT", Timeframe.H1, limit=200)
    """

    def __init__(
        self,
        exchange: MarketDataPort,
        repository: CandleRepositoryPort,
    ) -> None:
        self._exchange = exchange
        self._repo = repository

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[Candle]:
        """
        Return the most recent `limit` candles for a symbol/timeframe.

        Strategy:
        1. Query DB first.
        2. If DB is empty or stale (last candle > 2 * timeframe old),
           fetch from exchange and persist.
        3. Return from DB (single source of truth).
        """
        now = datetime.now(tz=UTC)
        lookback = since or (now - timedelta(seconds=timeframe.seconds * limit))

        # Step 1 — check DB
        cached = await self._repo.get_candles(
            symbol=symbol,
            exchange=self._exchange.exchange_id,
            timeframe=timeframe,
            start=lookback,
            limit=limit,
        )

        # Step 2 — refresh if stale or empty
        if self._is_stale(cached, timeframe, now):
            logger.info(
                "market_data_stale_refreshing",
                symbol=symbol,
                timeframe=timeframe.value,
                cached_count=len(cached),
            )
            fresh = await self._fetch_and_persist(symbol, timeframe, since=lookback, limit=limit)
            if fresh:
                cached = await self._repo.get_candles(
                    symbol=symbol,
                    exchange=self._exchange.exchange_id,
                    timeframe=timeframe,
                    start=lookback,
                    limit=limit,
                )

        logger.info(
            "market_data_served",
            symbol=symbol,
            timeframe=timeframe.value,
            count=len(cached),
        )
        return cached

    async def get_latest_price(self, symbol: str) -> Decimal:
        """
        Return the most recent traded price for a symbol in real time (ticker 'last').

        Unlike ``get_candles``, this returns an instant snapshot that is NOT tied
        to the last closed candle, so it changes tick-by-tick and matches the live
        chart / WebSocket feed. Used by the autonomous engine for real-time
        Stop-Loss / Take-Profit evaluation and order pricing.
        """
        price = await self._exchange.fetch_last_price(symbol)
        logger.info("market_data_latest_price", symbol=symbol, price=float(price))
        return price

    async def get_order_book(self, symbol: str, limit: int = 5) -> OrderBook:
        """Return a shallow public quote snapshot for descriptive spread measurement."""
        return await self._exchange.fetch_order_book(symbol=symbol, limit=limit)

    async def sync_symbol(
        self,
        symbol: str,
        timeframe: Timeframe,
        days_back: int = 30,
    ) -> int:
        """
        Full historical sync for a symbol: fetch `days_back` days and store.
        Returns number of candles persisted.
        """
        since = datetime.now(tz=UTC) - timedelta(days=days_back)
        candles = await self._fetch_and_persist(symbol, timeframe, since=since, limit=1000)
        logger.info(
            "symbol_synced",
            symbol=symbol,
            timeframe=timeframe.value,
            days_back=days_back,
            count=len(candles),
        )
        return len(candles)

    async def sync_all_symbols(
        self,
        symbols: list[str],
        timeframes: list[Timeframe],
        days_back: int = 7,
    ) -> dict[str, int]:
        """
        Sync multiple symbols x timeframes concurrently.
        Returns {symbol_timeframe: count}.
        """
        results: dict[str, int] = {}

        async def _sync_one(sym: str, tf: Timeframe) -> None:
            key = f"{sym}:{tf.value}"
            try:
                count = await self.sync_symbol(sym, tf, days_back=days_back)
                results[key] = count
            except Exception as exc:
                logger.error("sync_failed", symbol=sym, timeframe=tf.value, error=str(exc))
                results[key] = 0

        tasks = [_sync_one(s, tf) for s in symbols for tf in timeframes]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_stale(candles: list[Candle], timeframe: Timeframe, now: datetime) -> bool:
        """True if the most recent cached candle is older than 2x the timeframe duration."""
        if not candles:
            return True
        latest = max(c.timestamp for c in candles)
        # Make latest timezone-aware if needed
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        staleness_threshold = timedelta(seconds=timeframe.seconds * 2)
        return (now - latest) > staleness_threshold

    async def _fetch_and_persist(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime | None,
        limit: int,
    ) -> list[Candle]:
        """Fetch from exchange and persist to DB. Returns fetched candles."""
        try:
            candles = await self._exchange.fetch_historical_candles(
                symbol=symbol,
                timeframe=timeframe,
                since=since,
                limit=limit,
            )
            if candles:
                await self._repo.save_candles(candles)
                logger.info(
                    "candles_persisted",
                    symbol=symbol,
                    timeframe=timeframe.value,
                    count=len(candles),
                )
            return candles
        except Exception as exc:
            logger.error(
                "candle_fetch_failed",
                symbol=symbol,
                timeframe=timeframe.value,
                error=str(exc),
            )
            return []
