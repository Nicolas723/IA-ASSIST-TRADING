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
            "options": {
                "defaultType": self.market_type,
                # Binance rejects signed requests when the local clock drifts
                # ahead of the server. Let CCXT compute and apply the offset.
                "adjustForTimeDifference": True,
                "recvWindow": 10_000,
            },
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

    async def fetch_api_restrictions(self) -> dict[str, Any] | None:
        """Read the key's own permissions from Binance (read-only).

        Returns None where the exchange cannot report them: Binance Testnet
        has no ``/sapi`` endpoints and other exchanges use different APIs.
        """
        if self._exchange_id != Exchange.BINANCE or self.is_testnet:
            return None
        self._require_credentials()
        client = self._require_client()
        method = getattr(client, "sapi_get_account_apirestrictions", None)
        if method is None:
            return None
        # Loading markets also syncs the clock offset (adjustForTimeDifference);
        # without it Binance rejects the signed call with -1021.
        await self._ensure_markets(client)
        try:
            raw = await method()
        except Exception as exc:
            logger.warning(
                "api_restrictions_unavailable", exchange=self.exchange_id.value, error=str(exc)
            )
            raise ExchangeError(f"Could not read API key restrictions: {exc}") from exc
        return raw if isinstance(raw, dict) else None

    async def fetch_balances(self) -> dict[str, float]:
        """Query live balances (only assets with a non-dust amount)."""
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
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

    async def fetch_balance_details(self) -> dict[str, dict[str, float]]:
        """Return total/free/used per asset in a single exchange request."""
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
        try:
            raw = await client.fetch_balance()
        except Exception as exc:
            logger.error(
                "fetch_live_balance_failed", exchange=self.exchange_id.value, error=str(exc)
            )
            raise ExchangeError(f"Failed to fetch live balance: {exc}") from exc

        totals = raw.get("total", {}) or {}
        free = raw.get("free", {}) or {}
        used = raw.get("used", {}) or {}
        details: dict[str, dict[str, float]] = {}
        for currency in set(totals) | set(free) | set(used):
            total = float(totals.get(currency, 0.0) or 0.0)
            available = float(free.get(currency, 0.0) or 0.0)
            locked = float(used.get(currency, 0.0) or 0.0)
            if max(total, available, locked) > 0.00001:
                details[currency] = {
                    "total": total,
                    "free": available,
                    "used": locked,
                }
        return details

    async def fetch_free_balance(self, asset: str) -> float:
        """Return the free (available) balance for a single asset."""
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
        try:
            raw = await client.fetch_balance()
            return float(raw.get("free", {}).get(asset, 0.0) or 0.0)
        except Exception as exc:
            raise ExchangeError(f"Failed to fetch {asset} balance: {exc}") from exc

    async def fetch_order(
        self,
        symbol: str,
        *,
        client_order_id: str | None = None,
        order_id: str | None = None,
    ) -> Order | None:
        """Fetch a single order by exchange id or client id.

        Returns ``None`` when the exchange reports the order as unknown, which
        lets reconciliation distinguish "not found" from a network failure.
        """
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)

        params: dict[str, Any] = {}
        if client_order_id:
            if self._exchange_id == Exchange.BINANCE:
                params["origClientOrderId"] = client_order_id
            elif self._exchange_id == Exchange.BYBIT:
                params["orderLinkId"] = client_order_id
            else:
                params["clientOrderId"] = client_order_id

        try:
            response = await client.fetch_order(order_id, symbol, params)
        except ccxt.OrderNotFound:
            return None
        except Exception as exc:
            logger.warning(
                "live_fetch_order_failed",
                exchange=self.exchange_id.value,
                symbol=symbol,
                error=str(exc),
            )
            raise ExchangeError(f"Failed to fetch live order: {exc}") from exc

        status = _STATUS_MAP.get(str(response.get("status") or "").lower(), OrderStatus.OPEN)
        filled_raw = response.get("filled")
        filled = Decimal(str(filled_raw)) if filled_raw is not None else Decimal("0")
        requested_raw = response.get("amount")
        requested = Decimal(str(requested_raw)) if requested_raw is not None else filled
        if status == OrderStatus.FILLED and filled == 0 and requested > 0:
            filled = requested
        if status == OrderStatus.OPEN and 0 < filled < requested:
            status = OrderStatus.PARTIALLY_FILLED

        average_raw = response.get("average") or response.get("price")
        average = Decimal(str(average_raw)) if average_raw and filled > 0 else None
        fee = response.get("fee") or {}
        side_raw = str(response.get("side") or "buy").lower()
        type_raw = str(response.get("type") or "market").lower()

        return Order(
            id=uuid4(),
            symbol=symbol,
            exchange=self._exchange_id,
            mode=ExecutionMode.REAL,
            side=OrderSide.SELL if side_raw == "sell" else OrderSide.BUY,
            order_type=OrderType.LIMIT if "limit" in type_raw else OrderType.MARKET,
            quantity=requested or filled,
            status=status,
            filled_quantity=filled,
            average_fill_price=average,
            commission=Decimal(str(fee.get("cost") or 0)),
            commission_asset=str(fee.get("currency") or "USDT"),
            client_order_id=client_order_id,
            notes=f"Reconciled live {self._exchange_id.value} order ID: {response.get('id')}",
        )

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        """List exchange-side open orders, including ones this process never journaled.

        Only the caller can decide whether an order belongs to this system; the
        adapter reports every open order for the symbol and preserves the client
        order id so attribution is explicit rather than guessed.
        """
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
        try:
            responses = await client.fetch_open_orders(symbol)
        except Exception as exc:
            logger.warning(
                "live_fetch_open_orders_failed",
                exchange=self.exchange_id.value,
                symbol=symbol,
                error=str(exc),
            )
            raise ExchangeError(f"Failed to list live open orders: {exc}") from exc
        orders: list[Order] = []
        for response in responses or []:
            mapped = self._order_from_response(response)
            if mapped is not None:
                orders.append(mapped)
        return orders

    async def fetch_closed_orders(
        self,
        symbol: str,
        *,
        since_ms: int | None = None,
        limit: int = 50,
    ) -> list[Order]:
        """List recent closed orders for a symbol.

        Used only for ledger recovery: an order that filled while this process
        was down is no longer visible in ``fetch_open_orders``. The caller
        decides attribution by client order id prefix, so unrelated manual
        orders are still ignored.
        """
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
        try:
            responses = await client.fetch_closed_orders(symbol, since_ms, limit)
        except Exception as exc:
            logger.warning(
                "live_fetch_closed_orders_failed",
                exchange=self.exchange_id.value,
                symbol=symbol,
                error=str(exc),
            )
            raise ExchangeError(f"Failed to list live closed orders: {exc}") from exc
        orders: list[Order] = []
        for response in responses or []:
            mapped = self._order_from_response(response)
            if mapped is not None:
                orders.append(mapped)
        return orders

    def _order_from_response(self, response: dict[str, Any]) -> Order | None:
        """Map a CCXT order payload to the domain model without inventing fields."""
        symbol = response.get("symbol")
        if not symbol:
            return None
        info = response.get("info") or {}
        client_order_id = (
            response.get("clientOrderId")
            or info.get("clientOrderId")
            or info.get("origClientOrderId")
            or info.get("orderLinkId")
        )
        status = _STATUS_MAP.get(str(response.get("status") or "").lower(), OrderStatus.OPEN)
        filled_raw = response.get("filled")
        filled = Decimal(str(filled_raw)) if filled_raw is not None else Decimal("0")
        requested_raw = response.get("amount")
        requested = Decimal(str(requested_raw)) if requested_raw is not None else filled
        if status == OrderStatus.OPEN and 0 < filled < requested:
            status = OrderStatus.PARTIALLY_FILLED
        average_raw = response.get("average") or response.get("price")
        average = Decimal(str(average_raw)) if average_raw and filled > 0 else None
        limit_raw = response.get("price")
        stop_raw = response.get("stopPrice") or info.get("stopPrice")
        fee = response.get("fee") or {}
        side_raw = str(response.get("side") or "buy").lower()
        type_raw = str(response.get("type") or "market").lower()
        order_type = OrderType.LIMIT if "limit" in type_raw else OrderType.MARKET
        if stop_raw and "limit" in type_raw:
            order_type = OrderType.STOP_LIMIT
        elif stop_raw:
            order_type = OrderType.STOP_MARKET
        return Order(
            id=uuid4(),
            symbol=str(symbol),
            exchange=self._exchange_id,
            mode=ExecutionMode.REAL,
            side=OrderSide.SELL if side_raw == "sell" else OrderSide.BUY,
            order_type=order_type,
            quantity=requested or filled,
            limit_price=Decimal(str(limit_raw)) if limit_raw else None,
            stop_price=Decimal(str(stop_raw)) if stop_raw else None,
            status=status,
            filled_quantity=filled,
            average_fill_price=average,
            commission=Decimal(str(fee.get("cost") or 0)),
            commission_asset=str(fee.get("currency") or "USDT"),
            client_order_id=str(client_order_id) if client_order_id else None,
            notes=f"Discovered live {self._exchange_id.value} order ID: {response.get('id')}",
        )

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
        await self._ensure_markets(client)

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

    # ------------------------------------------------------------------
    # Binance spot OCO (exchange-side SL/TP protection)
    # ------------------------------------------------------------------

    async def _ensure_markets(self, client: ccxt.Exchange) -> None:
        if getattr(client, "markets", None):
            return
        try:
            # On Binance mainnet load_markets() first calls a *signed*
            # currencies endpoint and only syncs the clock afterwards, so a
            # local clock ahead of the server fails with -1021. Sync first.
            options = getattr(client, "options", None) or {}
            sync_clock = getattr(client, "load_time_difference", None)
            if options.get("adjustForTimeDifference") and sync_clock is not None:
                await sync_clock()
            await client.load_markets()
        except Exception as exc:
            raise ExchangeError(f"Failed to load exchange markets: {exc}") from exc

    async def _raw_symbol(self, client: ccxt.Exchange, symbol: str) -> str:
        await self._ensure_markets(client)
        try:
            market = client.market(symbol)
        except Exception as exc:
            raise ExchangeError(f"Unknown exchange market '{symbol}': {exc}") from exc
        raw = market.get("id") if isinstance(market, dict) else None
        if not raw:
            raise ExchangeError(f"Exchange market '{symbol}' has no raw symbol id")
        return str(raw)

    async def normalize_symbol(self, raw_symbol: str) -> str:
        """Convert an exchange raw symbol (e.g. BTCUSDT) to CCXT form (BTC/USDT)."""
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
        markets = getattr(client, "markets_by_id", None) or {}
        matches = markets.get(raw_symbol)
        if matches:
            return str(matches[0].get("symbol") or raw_symbol)
        try:
            return str(client.market(raw_symbol).get("symbol") or raw_symbol)
        except Exception:
            return raw_symbol

    async def create_oco_sell(
        self,
        symbol: str,
        quantity: float | Decimal,
        take_profit: float | Decimal,
        stop_loss: float | Decimal,
        stop_limit_price: float | Decimal | None = None,
        list_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Place a native Binance spot OCO to protect a long position.

        The OCO contains a take-profit limit sell and a stop-loss limit sell.
        Only one leg can execute. This is exchange-side protection: it remains
        active even if the backend is down.
        """
        if self._exchange_id != Exchange.BINANCE:
            raise ExchangeError("Native OCO protection is only implemented for Binance spot.")
        self._require_credentials()
        client = self._require_client()
        raw_symbol = await self._raw_symbol(client, symbol)

        try:
            amount = client.amount_to_precision(symbol, float(quantity))
            tp = client.price_to_precision(symbol, float(take_profit))
            sl = client.price_to_precision(symbol, float(stop_loss))
            stop_limit = client.price_to_precision(
                symbol, float(stop_limit_price if stop_limit_price is not None else stop_loss)
            )
        except Exception as exc:
            raise ExchangeError(f"Failed to normalize OCO values: {exc}") from exc

        if Decimal(amount) <= 0:
            raise ExchangeError("OCO quantity must be greater than zero.")
        if Decimal(tp) <= Decimal(sl):
            raise ExchangeError("OCO take-profit must be above stop-loss for a long position.")
        if Decimal(stop_limit) > Decimal(sl):
            stop_limit = sl
        if list_client_order_id and len(list_client_order_id) > 36:
            raise ExchangeError("list_client_order_id must be at most 36 characters.")

        params: dict[str, Any] = {
            "symbol": raw_symbol,
            "side": "SELL",
            "quantity": amount,
            "price": tp,
            "stopPrice": sl,
            "stopLimitPrice": stop_limit,
            "stopLimitTimeInForce": "GTC",
            "newOrderRespType": "FULL",
        }
        if list_client_order_id:
            params["listClientOrderId"] = list_client_order_id

        try:
            response = await client.private_post_order_oco(params)
        except ccxt.AuthenticationError as exc:
            raise SecurityError(f"Exchange authentication failed: {exc}") from exc
        except ccxt.InsufficientFunds as exc:
            raise ExchangeError(f"Insufficient funds for OCO: {exc}") from exc
        except ccxt.NetworkError as exc:
            raise ExchangeError(f"Network error while placing OCO: {exc}") from exc
        except Exception as exc:
            logger.error(
                "live_oco_creation_failed",
                exchange=self.exchange_id.value,
                symbol=symbol,
                error=str(exc),
            )
            raise ExchangeError(f"Failed to place OCO: {exc}") from exc
        return dict(response)

    async def cancel_oco(self, symbol: str, order_list_id: int | str) -> dict[str, Any]:
        """Cancel an open Binance OCO by order list id."""
        if self._exchange_id != Exchange.BINANCE:
            raise ExchangeError("Native OCO protection is only implemented for Binance spot.")
        self._require_credentials()
        client = self._require_client()
        raw_symbol = await self._raw_symbol(client, symbol)
        try:
            response = await client.private_delete_orderlist(
                {"symbol": raw_symbol, "orderListId": order_list_id}
            )
        except ccxt.NetworkError as exc:
            raise ExchangeError(f"Network error while cancelling OCO: {exc}") from exc
        except Exception as exc:
            logger.warning(
                "live_oco_cancel_failed",
                exchange=self.exchange_id.value,
                symbol=symbol,
                order_list_id=str(order_list_id),
                error=str(exc),
            )
            raise ExchangeError(f"Failed to cancel OCO: {exc}") from exc
        return dict(response)

    async def fetch_oco(self, symbol: str, order_list_id: int | str) -> dict[str, Any]:
        """Fetch the state of one OCO order list."""
        if self._exchange_id != Exchange.BINANCE:
            raise ExchangeError("Native OCO protection is only implemented for Binance spot.")
        self._require_credentials()
        client = self._require_client()
        # Binance's GET /api/v3/orderList accepts orderListId or
        # origClientOrderId, but not symbol. Sending symbol causes -1104.
        await self._ensure_markets(client)
        try:
            response = await client.private_get_orderlist({"orderListId": order_list_id})
        except ccxt.NetworkError as exc:
            raise ExchangeError(f"Network error while fetching OCO: {exc}") from exc
        except Exception as exc:
            raise ExchangeError(f"Failed to fetch OCO: {exc}") from exc

        data = dict(response)
        status = str(data.get("listOrderStatus") or data.get("listStatusType") or "").upper()
        legs = data.get("orders") or []
        if status == "EXECUTING" or not isinstance(legs, list):
            return data

        # Binance's orderList endpoint only returns symbol/orderId/clientOrderId
        # for each leg. Fetch the leg details before declaring a terminal OCO
        # unfilled, otherwise a filled stop-loss can be missed and the local
        # guard may sell the same position again.
        raw_symbol = await self._raw_symbol(client, symbol)
        enriched: list[Any] = []
        for leg in legs:
            if not isinstance(leg, dict):
                enriched.append(leg)
                continue
            if leg.get("executedQty") is not None or leg.get("executedQuantity") is not None:
                enriched.append(leg)
                continue
            order_id = leg.get("orderId")
            if order_id is None:
                raise ExchangeError(
                    "OCO leg is missing orderId; cannot verify whether it executed."
                )
            try:
                detail = await client.private_get_order({"symbol": raw_symbol, "orderId": order_id})
            except ccxt.NetworkError as exc:
                raise ExchangeError(f"Network error while fetching OCO leg: {exc}") from exc
            except Exception as exc:
                raise ExchangeError(f"Failed to fetch OCO leg: {exc}") from exc
            if not isinstance(detail, dict):
                raise ExchangeError("Unexpected OCO leg response from exchange.")
            enriched.append({**leg, **detail})
        data["orders"] = enriched
        return data

    async def fetch_open_oco_lists(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """List open Binance OCO order lists (all symbols when symbol is None)."""
        if self._exchange_id != Exchange.BINANCE:
            raise ExchangeError("Native OCO protection is only implemented for Binance spot.")
        self._require_credentials()
        client = self._require_client()
        await self._ensure_markets(client)
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = await self._raw_symbol(client, symbol)
        try:
            response = await client.private_get_openorderlist(params)
        except ccxt.NetworkError as exc:
            raise ExchangeError(f"Network error while listing open OCOs: {exc}") from exc
        except Exception as exc:
            raise ExchangeError(f"Failed to list open OCOs: {exc}") from exc
        if not isinstance(response, list):
            return []
        return [dict(item) for item in response if isinstance(item, dict)]

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
