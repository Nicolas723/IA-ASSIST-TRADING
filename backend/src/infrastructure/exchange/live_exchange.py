"""
Generic live exchange adapter (CCXT) with hard safety interlocks.

Supports any CCXT exchange id, but the platform currently exposes Binance and
Bybit. The adapter never stores credentials: callers decrypt them from the
vault and pass them in memory.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import ccxt.async_support as ccxt

from src.domain.market.entities import Exchange
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.shared.exceptions.domain import ExchangeError, SecurityError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

_STATUS_MAP: dict[str, OrderStatus] = {
    "closed": OrderStatus.FILLED,
    "filled": OrderStatus.FILLED,
    "open": OrderStatus.OPEN,
    "new": OrderStatus.OPEN,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


class LiveExchangeAdapter:
    """Async CCXT client for real order execution and account queries."""

    def __init__(
        self,
        exchange: Exchange,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str | None = None,
        is_testnet: bool = True,
        market_type: str = "spot",
        timeout_ms: int = 15_000,
    ) -> None:
        self._exchange_id = exchange
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.is_testnet = is_testnet
        self.market_type = market_type
        self.timeout_ms = timeout_ms
        self._client: ccxt.Exchange | None = None
        self._init_client()

    @property
    def exchange_id(self) -> Exchange:
        return self._exchange_id

    def _init_client(self) -> None:
        factory = getattr(ccxt, self._exchange_id.value, None)
        if factory is None:
            raise ExchangeError(f"Exchange '{self._exchange_id.value}' is not supported by CCXT")

        config: dict[str, Any] = {
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "enableRateLimit": True,
            "timeout": self.timeout_ms,
            "options": {"defaultType": self.market_type},
        }
        if self.passphrase:
            config["password"] = self.passphrase

        client = factory(config)
        if self.is_testnet:
            client.set_sandbox_mode(True)
        self._client = client

    def update_credentials(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool = True,
        passphrase: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.is_testnet = is_testnet
        self.passphrase = passphrase
        self._init_client()

    def _require_client(self) -> ccxt.Exchange:
        if self._client is None:
            self._init_client()
        if self._client is None:
            raise ExchangeError("Exchange client is not initialized")
        return self._client

    def _require_credentials(self) -> None:
        if not self.api_key or not self.api_secret:
            raise SecurityError("Exchange API credentials are not configured in the vault.")

    async def ping(self) -> dict[str, Any]:
        """Test connectivity and return server latency in milliseconds."""
        client = self._require_client()
        start = time.monotonic()
        server_time = await client.fetch_time()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {
            "status": "connected",
            "exchange": self._exchange_id.value,
            "is_testnet": self.is_testnet,
            "server_time_ms": server_time,
            "latency_ms": latency_ms,
        }

    async def fetch_balances(self) -> dict[str, float]:
        """Query live balances (only assets with a non-dust amount)."""
        self._require_credentials()
        client = self._require_client()
        try:
            raw = await client.fetch_balance()
            balances: dict[str, float] = {}
            for currency, value in raw.get("total", {}).items():
                if float(value or 0) > 0.00001:
                    balances[currency] = float(value)
            return balances
        except Exception as exc:
            logger.error(
                "fetch_live_balance_failed", exchange=self.exchange_id.value, error=str(exc)
            )
            raise ExchangeError(f"Failed to fetch live balance: {exc}") from exc

    async def fetch_free_balance(self, asset: str) -> float:
        """Return the free (available) balance for a single asset."""
        self._require_credentials()
        client = self._require_client()
        try:
            raw = await client.fetch_balance()
            return float(raw.get("free", {}).get(asset, 0.0) or 0.0)
        except Exception as exc:
            raise ExchangeError(f"Failed to fetch {asset} balance: {exc}") from exc

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        """Submit a real order and map the CCXT response to a domain Order."""
        self._require_credentials()
        client = self._require_client()

        is_stop = order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT)
        ccxt_type = "limit" if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else "market"
        ccxt_side = "buy" if side == OrderSide.BUY else "sell"

        # Respect exchange lot-size / tick-size rules when available.
        try:
            amount: float = float(client.amount_to_precision(symbol, quantity))
        except Exception:
            amount = quantity

        params: dict[str, Any] = {}
        if is_stop and stop_price is not None:
            try:
                params["stopPrice"] = float(client.price_to_precision(symbol, stop_price))
            except Exception:
                params["stopPrice"] = float(stop_price)
        if client_order_id:
            if self._exchange_id == Exchange.BINANCE:
                params["newClientOrderId"] = client_order_id
            elif self._exchange_id == Exchange.BYBIT:
                params["orderLinkId"] = client_order_id
            else:
                params["clientOrderId"] = client_order_id

        limit_price: float | None = None
        if ccxt_type == "limit":
            if price is None:
                raise ExchangeError("A limit price is required for limit orders")
            try:
                limit_price = float(client.price_to_precision(symbol, price))
            except Exception:
                limit_price = price

        try:
            response = await client.create_order(
                symbol=symbol,
                type=ccxt_type,
                side=ccxt_side,
                amount=amount,
                price=limit_price,
                params=params,
            )
        except ccxt.AuthenticationError as exc:
            raise SecurityError(f"Exchange authentication failed: {exc}") from exc
        except ccxt.InsufficientFunds as exc:
            raise ExchangeError(f"Insufficient funds: {exc}") from exc
        except ccxt.NetworkError as exc:
            raise ExchangeError(f"Network error while placing order: {exc}") from exc
        except Exception as exc:
            logger.error(
                "live_order_execution_failed", exchange=self.exchange_id.value, error=str(exc)
            )
            raise ExchangeError(f"Live order failed: {exc}") from exc

        raw_status = str(response.get("status") or "").lower()
        status = _STATUS_MAP.get(raw_status, OrderStatus.OPEN)
        filled_raw = response.get("filled")
        filled_qty = Decimal(str(filled_raw)) if filled_raw is not None else Decimal("0")
        requested_qty = Decimal(str(amount))
        if not raw_status and filled_qty > 0:
            status = (
                OrderStatus.FILLED if filled_qty >= requested_qty else OrderStatus.PARTIALLY_FILLED
            )
        if status == OrderStatus.FILLED and filled_qty == 0:
            filled_qty = requested_qty
        elif status == OrderStatus.OPEN and filled_qty >= requested_qty > 0:
            status = OrderStatus.FILLED

        fill_price_raw = response.get("average") or response.get("price") or price
        fill_price = Decimal(str(fill_price_raw)) if fill_price_raw and filled_qty > 0 else None
        fee = response.get("fee") or {}
        commission = Decimal(str(fee.get("cost") or 0))
        exchange_order_id = response.get("id")
        now = datetime.now(tz=UTC)

        return Order(
            id=uuid4(),
            symbol=symbol,
            exchange=self._exchange_id,
            mode=ExecutionMode.REAL,
            side=side,
            order_type=order_type,
            quantity=Decimal(str(quantity)),
            limit_price=Decimal(str(limit_price)) if limit_price is not None else None,
            stop_price=Decimal(str(stop_price)) if stop_price is not None else None,
            status=status,
            filled_quantity=filled_qty,
            average_fill_price=fill_price,
            commission=commission,
            commission_asset=str(fee.get("currency") or "USDT"),
            stop_loss=None,
            take_profit=None,
            client_order_id=client_order_id,
            submitted_at=now,
            filled_at=now if status == OrderStatus.FILLED else None,
            notes=(
                f"Live {self._exchange_id.value} order ID: {exchange_order_id}"
                + (f" | client_order_id={client_order_id}" if client_order_id else "")
            ),
        )

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
