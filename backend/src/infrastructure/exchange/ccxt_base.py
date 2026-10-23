"""
CCXT Base Adapter.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import ccxt.async_support as ccxt

from src.domain.market.entities import OHLCV, Candle, Exchange, SymbolStr, Tick, Timeframe
from src.domain.market.ports import MarketDataPort
from src.shared.exceptions.domain import ExchangeError, ExchangeRateLimitError
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger(__name__)


class CcxtBaseAdapter(MarketDataPort):
    """Base adapter using CCXT for market data."""

    def __init__(self, exchange_id: Exchange, ccxt_exchange: ccxt.Exchange) -> None:
        self._exchange_id = exchange_id
        self._client = ccxt_exchange

    @property
    def exchange_id(self) -> Exchange:
        return self._exchange_id

    async def fetch_historical_candles(
        self,
        symbol: SymbolStr,
        timeframe: Timeframe,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        try:
            since_ms = int(since.timestamp() * 1000) if since else None

            # CCXT expects timeframe as a string matching its own (e.g., '1h')
            # Our Timeframe enum values are designed to match CCXT.
            ohlcvs = await self._client.fetch_ohlcv(
                symbol, timeframe.value, since=since_ms, limit=limit
            )

            candles = []
            for o in ohlcvs:
                ts = datetime.fromtimestamp(o[0] / 1000.0, tz=UTC)
                candles.append(
                    Candle(
                        symbol=symbol,
                        exchange=self._exchange_id,
                        timeframe=timeframe,
                        timestamp=ts,
                        ohlcv=OHLCV(
                            open=Decimal(str(o[1])),
                            high=Decimal(str(o[2])),
                            low=Decimal(str(o[3])),
                            close=Decimal(str(o[4])),
                            volume=Decimal(str(o[5])),
                        ),
                        is_closed=True,  # Assuming historical data is closed candles
                    )
                )
            return candles

        except ccxt.RateLimitExceeded as e:
            raise ExchangeRateLimitError(str(e), exchange=self.exchange_id.value) from e
        except ccxt.NetworkError as e:
            raise ExchangeError(f"Network error: {e}", exchange=self.exchange_id.value) from e
        except Exception as e:
            raise ExchangeError(
                f"Error fetching candles: {e}", exchange=self.exchange_id.value
            ) from e

    async def fetch_last_price(self, symbol: SymbolStr) -> Decimal:
        """Return the most recent traded price from the exchange ticker (REST)."""
        try:
            ticker = await self._client.fetch_ticker(symbol)
        except ccxt.RateLimitExceeded as e:
            raise ExchangeRateLimitError(str(e), exchange=self.exchange_id.value) from e
        except ccxt.NetworkError as e:
            raise ExchangeError(f"Network error: {e}", exchange=self.exchange_id.value) from e
        except Exception as e:
            raise ExchangeError(
                f"Error fetching ticker: {e}", exchange=self.exchange_id.value
            ) from e

        last = ticker.get("last") if isinstance(ticker, dict) else None
        if last is None:
            raise ExchangeError(
                f"Ticker response for {symbol} did not include a 'last' price",
                exchange=self.exchange_id.value,
            )
        return Decimal(str(last))

    async def stream_candles(
        self, symbol: SymbolStr, timeframe: Timeframe
    ) -> AsyncGenerator[Candle]:
        """Watch OHLCV via CCXT Pro (requires CCXT Pro or exchange specific WS implementation)."""
        # Note: CCXT async websocket requires CCXT Pro for some exchanges, or watch_ohlcv method.
        # For this base implementation, we'll use watch_ohlcv if available.
        if not self._client.has.get("watchOHLCV"):
            raise ExchangeError(
                f"watchOHLCV not supported by {self.exchange_id.value}",
                exchange=self.exchange_id.value,
            )

        while True:
            try:
                # CCXT watch_ohlcv returns an array of candles
                ohlcvs = await self._client.watch_ohlcv(symbol, timeframe.value)

                # Usually returns the latest candle (might be open or just closed)
                # We need to yield it
                for o in ohlcvs:
                    ts = datetime.fromtimestamp(o[0] / 1000.0, tz=UTC)
                    yield Candle(
                        symbol=symbol,
                        exchange=self._exchange_id,
                        timeframe=timeframe,
                        timestamp=ts,
                        ohlcv=OHLCV(
                            open=Decimal(str(o[1])),
                            high=Decimal(str(o[2])),
                            low=Decimal(str(o[3])),
                            close=Decimal(str(o[4])),
                            volume=Decimal(str(o[5])),
                        ),
                        is_closed=False,  # We can't be certain if it's closed without tracking
                    )
            except ccxt.NetworkError:
                logger.warning("ws_network_error", exchange=self.exchange_id.value)
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("ws_stream_error", error=str(e), exchange=self.exchange_id.value)
                await asyncio.sleep(5)

    async def stream_ticks(self, symbol: SymbolStr) -> AsyncGenerator[Tick]:
        if not self._client.has.get("watchTrades"):
            raise ExchangeError(
                f"watchTrades not supported by {self.exchange_id.value}",
                exchange=self.exchange_id.value,
            )

        while True:
            try:
                trades = await self._client.watch_trades(symbol)
                for t in trades:
                    ts = datetime.fromtimestamp(t["timestamp"] / 1000.0, tz=UTC)
                    yield Tick(
                        symbol=symbol,
                        exchange=self._exchange_id,
                        timestamp=ts,
                        price=Decimal(str(t["price"])),
                        volume=Decimal(str(t["amount"])),
                        side=t.get("side"),
                    )
            except ccxt.NetworkError:
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("ws_stream_error", error=str(e), exchange=self.exchange_id.value)
                await asyncio.sleep(5)

    async def close(self) -> None:
        await self._client.close()
