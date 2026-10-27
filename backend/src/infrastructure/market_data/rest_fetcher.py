"""
REST Fetcher with automatic retry and exponential backoff.

Wraps exchange adapters to add:
- Retry logic (configurable attempts + backoff)
- Rate limit awareness (respects CCXT rate limiter)
- Structured logging per attempt
- Circuit breaker integration (Phase 4)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.domain.market.ports import MarketDataPort
from src.shared.exceptions.domain import ExchangeError, ExchangeRateLimitError
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import datetime
    from decimal import Decimal

    from src.domain.market.entities import Candle, Exchange, OrderBook, Tick, Timeframe

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds
_BACKOFF_FACTOR = 2.0


class RetryingMarketDataFetcher(MarketDataPort):
    """
    Decorator around any MarketDataPort that adds retry / backoff logic.

    Usage:
        raw_adapter = BinanceAdapter()
        fetcher = RetryingMarketDataFetcher(raw_adapter)
        candles = await fetcher.fetch_historical_candles("BTC/USDT", Timeframe.H1)
    """

    def __init__(
        self,
        inner: MarketDataPort,
        max_retries: int = _MAX_RETRIES,
        base_delay: float = _BASE_DELAY,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay = base_delay

    @property
    def exchange_id(self) -> Exchange:
        return self._inner.exchange_id

    async def fetch_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """Fetch OHLCV with retry on transient errors."""
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                result = await self._inner.fetch_historical_candles(
                    symbol, timeframe, since=since, limit=limit
                )
                if attempt > 1:
                    logger.info(
                        "fetch_succeeded_after_retry",
                        symbol=symbol,
                        timeframe=timeframe.value,
                        attempt=attempt,
                    )
                return result

            except ExchangeRateLimitError as exc:
                # Rate limit: always retry with longer wait
                wait = self._base_delay * (self._backoff_factor**attempt) * 2
                logger.warning(
                    "rate_limit_hit_retrying",
                    symbol=symbol,
                    attempt=attempt,
                    wait_seconds=wait,
                )
                await asyncio.sleep(wait)
                last_error = exc

            except ExchangeError as exc:
                if attempt == self._max_retries:
                    logger.error(
                        "fetch_failed_all_retries",
                        symbol=symbol,
                        timeframe=timeframe.value,
                        attempts=self._max_retries,
                        error=str(exc),
                    )
                    raise

                wait = self._base_delay * (self._backoff_factor ** (attempt - 1))
                logger.warning(
                    "fetch_error_retrying",
                    symbol=symbol,
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
                last_error = exc

        # Should not reach here, but satisfy type checker
        if last_error:
            raise last_error
        return []

    async def fetch_last_price(self, symbol: str) -> Decimal:
        """Fetch the latest ticker price with retry on transient errors."""
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._inner.fetch_last_price(symbol)

            except ExchangeRateLimitError as exc:
                wait = self._base_delay * (self._backoff_factor**attempt) * 2
                logger.warning(
                    "rate_limit_hit_retrying_price",
                    symbol=symbol,
                    attempt=attempt,
                    wait_seconds=wait,
                )
                await asyncio.sleep(wait)
                last_error = exc

            except ExchangeError as exc:
                if attempt == self._max_retries:
                    logger.error(
                        "price_fetch_failed_all_retries",
                        symbol=symbol,
                        attempts=self._max_retries,
                        error=str(exc),
                    )
                    raise

                wait = self._base_delay * (self._backoff_factor ** (attempt - 1))
                logger.warning(
                    "price_fetch_error_retrying",
                    symbol=symbol,
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
                last_error = exc

        if last_error:
            raise last_error
        raise ExchangeError(
            f"Unable to fetch last price for {symbol}", exchange=self.exchange_id.value
        )

    async def fetch_last_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        """Batch prices with retry on rate limits and transient errors."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._inner.fetch_last_prices(symbols)
            except ExchangeRateLimitError as exc:
                last_error = exc
                await asyncio.sleep(self._base_delay * (self._backoff_factor**attempt) * 2)
            except ExchangeError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._base_delay * self._backoff_factor ** (attempt - 1))
        raise ExchangeError(
            f"Batch price request failed after {self._max_retries} attempts: {last_error}",
            exchange=self.exchange_id.value,
        )

    async def fetch_order_book(self, symbol: str, limit: int = 5) -> OrderBook:
        """Retry transient failures fetching the small public order-book snapshot."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._inner.fetch_order_book(symbol, limit=limit)
            except ExchangeRateLimitError as exc:
                last_error = exc
                await asyncio.sleep(self._base_delay * (self._backoff_factor**attempt) * 2)
            except ExchangeError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._base_delay * self._backoff_factor ** (attempt - 1))
        raise ExchangeError(
            f"Order-book request failed after {self._max_retries} attempts: {last_error}",
            exchange=self.exchange_id.value,
        )

    @property
    def _backoff_factor(self) -> float:
        return _BACKOFF_FACTOR

    # Delegate streaming methods directly (no retry wrapper — streams handle their own reconnect)
    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncGenerator[Candle]:
        async for candle in self._inner.stream_candles(symbol, timeframe):
            yield candle

    async def stream_ticks(self, symbol: str) -> AsyncGenerator[Tick]:
        async for tick in self._inner.stream_ticks(symbol):
            yield tick
