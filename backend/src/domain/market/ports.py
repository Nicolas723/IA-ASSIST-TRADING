"""
Market domain ports (interfaces).

These interfaces define the contract that the infrastructure layer must fulfill.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime
    from decimal import Decimal

    from src.domain.market.entities import Candle, Exchange, SymbolStr, Tick, Timeframe


class MarketDataPort(abc.ABC):
    """
    Interface for fetching market data from an exchange.
    Implemented by exchange adapters (e.g., CCXT Binance Adapter).
    """

    @property
    @abc.abstractmethod
    def exchange_id(self) -> Exchange:
        """The exchange this adapter connects to."""
        ...

    @abc.abstractmethod
    async def fetch_historical_candles(
        self,
        symbol: SymbolStr,
        timeframe: Timeframe,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """Fetch historical OHLCV data (REST API)."""
        ...

    @abc.abstractmethod
    async def fetch_last_price(self, symbol: SymbolStr) -> Decimal:
        """
        Fetch the most recent traded price for a symbol (ticker 'last', REST API).

        This is a real-time instant snapshot and does NOT depend on the last
        closed candle, so it changes tick-by-tick within the current period.
        """
        ...

    @abc.abstractmethod
    def stream_candles(self, symbol: SymbolStr, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Stream real-time closed candles via WebSockets."""
        ...

    @abc.abstractmethod
    def stream_ticks(self, symbol: SymbolStr) -> AsyncIterator[Tick]:
        """Stream real-time price ticks via WebSockets."""
        ...


class CandleRepositoryPort(abc.ABC):
    """
    Interface for persisting and querying historical candles.
    Implemented by TimescaleDB repository.
    """

    @abc.abstractmethod
    async def save_candles(self, candles: list[Candle]) -> None:
        """Save a batch of candles to the database."""
        ...

    @abc.abstractmethod
    async def get_candles(
        self,
        symbol: SymbolStr,
        exchange: Exchange,
        timeframe: Timeframe,
        start: datetime,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """Retrieve historical candles from the database."""
        ...
