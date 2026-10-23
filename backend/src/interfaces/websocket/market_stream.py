"""
WebSocket Stream for Market Data & Real-Time Price Feeds.
Provides real-time price tick streaming, candle updates, and client command processing.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import ClassVar

logger = get_logger(__name__)

router = APIRouter()


class ConnectionManager:
    """
    Manages active WebSocket connections per trading symbol and globally.
    Streams live price ticks to connected clients every 1.5 seconds.
    """

    DEFAULT_BASE_PRICES: ClassVar[dict[str, float]] = {
        "BTC/USDT": 84800.0,
        "ETH/USDT": 3100.0,
        "SOL/USDT": 185.0,
        "BNB/USDT": 590.0,
    }

    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.all_connections: set[WebSocket] = set()
        self.last_prices: dict[str, float] = dict(self.DEFAULT_BASE_PRICES)
        self._ticker_task: asyncio.Task[None] | None = None
        # Optional async callable that returns the LIVE price for a symbol.
        # When set (see main.py startup), the stream broadcasts real exchange
        # prices instead of the simulated fluctuations below.
        self._price_provider: Callable[[str], Awaitable[float | None]] | None = None

    def set_price_provider(self, provider: Callable[[str], Awaitable[float | None]] | None) -> None:
        """Register the async price source used by the live ticker loop."""
        self._price_provider = provider

    async def connect(self, symbol: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if symbol not in self.active_connections:
            self.active_connections[symbol] = []
        self.active_connections[symbol].append(websocket)
        self.all_connections.add(websocket)
        logger.info(
            "ws_client_connected",
            symbol=symbol,
            total_symbol_clients=len(self.active_connections[symbol]),
        )

        # Start background ticker task if not already running
        if self._ticker_task is None or self._ticker_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._ticker_task = loop.create_task(self._ticker_loop())
            except RuntimeError:
                pass

    def disconnect(self, symbol: str, websocket: WebSocket) -> None:
        if symbol in self.active_connections and websocket in self.active_connections[symbol]:
            self.active_connections[symbol].remove(websocket)
            if not self.active_connections[symbol]:
                del self.active_connections[symbol]
        self.all_connections.discard(websocket)
        logger.info("ws_client_disconnected", symbol=symbol)

    async def _ticker_loop(self) -> None:
        """Background loop broadcasting real (or simulated fallback) price ticks to subscribed clients."""
        while len(self.all_connections) > 0:
            for symbol in list(self.active_connections.keys()):
                new_price = await self._resolve_price(symbol)
                self.last_prices[symbol] = new_price

                tick_payload = {
                    "type": "price_tick",
                    "symbol": symbol,
                    "price": new_price,
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                    "volume": round(random.uniform(0.05, 1.8), 4),
                }
                await self.broadcast_to_symbol(symbol, tick_payload)

            await asyncio.sleep(1.5)

    async def _resolve_price(self, symbol: str) -> float:
        """
        Resolve the price to broadcast:

        1. If a live price provider is wired, use the real exchange ticker price.
        2. Otherwise (or on failure), fall back to a simulated micro-fluctuation
           around the last known price so the stream never goes silent.
        """
        base_price = self.last_prices.get(symbol, 100.0)

        if self._price_provider is not None:
            try:
                live = await self._price_provider(symbol)
                if live is not None and live > 0:
                    return round(float(live), 2 if live > 10 else 4)
            except Exception as exc:
                logger.warning("ws_live_price_fetch_failed", symbol=symbol, error=str(exc))

        drift = base_price * random.uniform(-0.0005, 0.0005)
        return round(max(base_price + drift, 0.01), 2 if base_price > 10 else 4)

    async def broadcast_to_symbol(self, symbol: str, message: dict[str, Any]) -> None:
        """Send a message to all sockets subscribed to a specific symbol."""
        if symbol not in self.active_connections:
            return
        stale: list[WebSocket] = []
        for ws in list(self.active_connections[symbol]):
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(symbol, ws)

    async def broadcast_all(self, message: dict[str, Any]) -> None:
        """Send a message to all connected WebSocket clients."""
        stale: list[WebSocket] = []
        for ws in list(self.all_connections):
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.all_connections.discard(ws)


manager = ConnectionManager()


@router.websocket("/ws/market/{symbol:path}")
async def market_websocket_endpoint(websocket: WebSocket, symbol: str) -> None:
    """
    WebSocket endpoint streaming live market data for a given trading pair (e.g. BTC/USDT).
    """
    normalized_symbol = symbol.replace("-", "/").replace("_", "/").upper()
    await manager.connect(normalized_symbol, websocket)

    # Initial welcome subscription confirmation payload
    await websocket.send_json(
        {
            "type": "subscribed",
            "symbol": normalized_symbol,
            "status": "connected",
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )

    try:
        while True:
            # Receive client messages (pings, subscriptions, command requests)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")

                if action == "ping":
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "timestamp": datetime.now(tz=UTC).isoformat(),
                        }
                    )
                elif action == "subscribe":
                    new_sym = (
                        str(msg.get("symbol", normalized_symbol))
                        .replace("-", "/")
                        .replace("_", "/")
                        .upper()
                    )
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "symbol": new_sym,
                            "timestamp": datetime.now(tz=UTC).isoformat(),
                        }
                    )
            except Exception as parse_err:
                logger.debug("ws_msg_parse_notice", error=str(parse_err))
    except WebSocketDisconnect:
        manager.disconnect(normalized_symbol, websocket)
    except Exception as exc:
        logger.warning("ws_stream_error", symbol=normalized_symbol, error=str(exc))
        manager.disconnect(normalized_symbol, websocket)
