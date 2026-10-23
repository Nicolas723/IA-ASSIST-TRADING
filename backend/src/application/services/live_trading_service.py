"""
LiveTradingService — real-money (or testnet) execution with hard safety rails.

This service is deliberately separate from ``TradingService`` (paper/backtest):
real execution is asynchronous, needs vault credentials and must enforce extra
security interlocks. It also keeps a lightweight in-memory ledger of filled
orders so the journal, risk checks and protective exits have something to work
with. Exchange balances remain the source of truth for the account view.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel

from src.domain.market.entities import Exchange
from src.domain.notifications.entities import AlertSeverity
from src.domain.risk.entities import OrderRiskCheckRequest
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
from src.infrastructure.exchange.live_exchange import LiveExchangeAdapter
from src.shared.config.settings import get_settings
from src.shared.exceptions.domain import (
    ExchangeError,
    RiskViolationError,
    SecurityError,
    ValidationError,
)
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from uuid import UUID

    from src.application.services.alert_service import AlertService
    from src.application.services.risk_service import RiskService
    from src.application.services.vault_service import VaultService
    from src.infrastructure.exchange.paper import PlaceOrderRequest
    from src.shared.config.settings import Settings

logger = get_logger(__name__)

PriceFetcher = Callable[[str], "Awaitable[float | None]"]
ExecutionCallback = Callable[[Order], "Awaitable[None]"]
ReconcileCallback = Callable[[], "Awaitable[None]"]


class LiveTradingStatus(BaseModel):
    """Readiness snapshot exposed to the UI before enabling real trading."""

    enabled: bool
    execution_mode: str
    exchange: str
    testnet: bool
    mainnet_allowed: bool
    credentials_configured: bool
    allowed_symbols: list[str]
    max_notional: Decimal
    require_manual_confirmation: bool
    autonomous_real_enabled: bool
    open_positions: int = 0
    guard_running: bool = False
    protection_mode: str = "local_guard"
    reconciliation_interval_seconds: int = 300


class LiveTradingService:
    """Execute real orders through CCXT while enforcing platform safeguards."""

    def __init__(
        self,
        vault_service: VaultService,
        risk_service: RiskService | None = None,
        alert_service: AlertService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.vault_service = vault_service
        self.risk_service = risk_service
        self.alert_service = alert_service
        self.settings = settings or get_settings()

        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._trades: list[Trade] = []
        self._usdt_balance: Decimal | None = None
        self._realized_pnl = Decimal("0")
        self._peak_value = Decimal("0")
        self._lock = asyncio.Lock()
        self._closing: set[UUID] = set()
        self._order_ids: set[UUID] = set()
        self._client_order_ids: dict[str, Order] = {}
        self._last_reconcile = 0.0

        self._guard_task: asyncio.Task[None] | None = None
        self._price_fetcher: PriceFetcher | None = None
        self._on_execution: ExecutionCallback | None = None
        self._on_reconcile: ReconcileCallback | None = None

    # ------------------------------------------------------------------
    # Configuration / readiness
    # ------------------------------------------------------------------

    def _exchange(self, exchange: Exchange | str | None = None) -> Exchange:
        raw = exchange or self.settings.real_trading_exchange
        try:
            resolved = Exchange(raw)
        except ValueError as exc:
            raise ValidationError(f"Unsupported real-trading exchange: {raw}") from exc
        if resolved in (Exchange.PAPER, Exchange.BACKTEST):
            raise ValidationError("Paper/backtest exchanges cannot be used for real trading.")
        return resolved

    def status(self, exchange: Exchange | str | None = None) -> LiveTradingStatus:
        exchange_id = self._exchange(exchange)
        summary = self.vault_service.vault.get_credential_summary(exchange_id.value)
        return LiveTradingStatus(
            enabled=self.settings.real_trading_enabled,
            execution_mode=self.settings.execution_mode.value,
            exchange=exchange_id.value,
            testnet=summary.is_testnet if summary else True,
            mainnet_allowed=self.settings.allow_mainnet_trading,
            credentials_configured=summary is not None,
            allowed_symbols=list(self.settings.real_trading_allowed_symbols),
            max_notional=self.settings.real_trading_max_notional,
            require_manual_confirmation=self.settings.require_manual_confirmation,
            autonomous_real_enabled=self.settings.enable_autonomous_real_trading,
            open_positions=len(self._positions),
            guard_running=self._guard_task is not None and not self._guard_task.done(),
            protection_mode="local_guard",
            reconciliation_interval_seconds=(self.settings.real_trading_reconcile_interval_seconds),
        )

    def _credentials(self, exchange: Exchange) -> tuple[str, str, str | None]:
        creds = self.vault_service.vault.get_decrypted_credential(exchange.value)
        if not creds:
            raise SecurityError(
                f"No credentials configured for {exchange.value}. Save API keys in the vault first."
            )
        return creds

    def _assert_can_trade(self, exchange: Exchange, confirm: bool) -> None:
        if not self.settings.real_trading_enabled:
            raise SecurityError(
                "Real trading is disabled. Set EXECUTION_MODE=real and ENABLE_REAL_TRADING=true."
            )
        summary = self.vault_service.vault.get_credential_summary(exchange.value)
        if summary is None:
            raise SecurityError(f"No credentials configured for {exchange.value}.")
        if not summary.is_testnet and not self.settings.allow_mainnet_trading:
            raise SecurityError(
                "Mainnet credentials detected. Set ALLOW_MAINNET_TRADING=true to trade real funds."
            )
        if self.settings.require_manual_confirmation and not confirm:
            raise SecurityError(
                "Manual confirmation required. Send confirm=true to place a real order."
            )

    def _assert_order_allowed(self, symbol: str, notional: Decimal) -> None:
        allowed = [item.upper() for item in self.settings.real_trading_allowed_symbols]
        if allowed and symbol.upper() not in allowed:
            raise SecurityError(
                f"Symbol {symbol} is not in REAL_TRADING_ALLOWED_SYMBOLS: {', '.join(allowed)}"
            )
        if notional > self.settings.real_trading_max_notional:
            raise SecurityError(
                f"Order notional ${notional:,.2f} exceeds REAL_TRADING_MAX_NOTIONAL "
                f"(${self.settings.real_trading_max_notional:,.2f})."
            )

    def _adapter(self, exchange: Exchange) -> LiveExchangeAdapter:
        api_key, api_secret, passphrase = self._credentials(exchange)
        summary = self.vault_service.vault.get_credential_summary(exchange.value)
        return LiveExchangeAdapter(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=summary.is_testnet if summary else True,
        )

    def restore(
        self,
        *,
        positions: list[Position],
        orders: list[Order],
        trades: list[Trade],
        available_balance: Decimal | None = None,
        realized_pnl: Decimal | None = None,
        peak_value: Decimal | None = None,
    ) -> None:
        """Replace the in-memory ledger with the journal snapshot."""
        self._positions = {
            position.symbol: position
            for position in positions
            if position.mode == ExecutionMode.REAL
        }
        self._orders = [order for order in orders if order.mode == ExecutionMode.REAL]
        self._trades = [trade for trade in trades if trade.mode == ExecutionMode.REAL]
        self._order_ids = {order.id for order in self._orders}
        self._client_order_ids = {}
        for order in self._orders:
            if order.client_order_id:
                self._client_order_ids[order.client_order_id] = order
        if available_balance is not None:
            self._usdt_balance = available_balance
        if realized_pnl is not None:
            self._realized_pnl = realized_pnl
        if peak_value is not None:
            self._peak_value = peak_value
        self._last_reconcile = 0.0

    async def notify_restored_positions(self) -> None:
        """Warn when restored real positions cannot be protected automatically."""
        if not self._positions:
            return
        if not self.settings.real_trading_enabled:
            await self._alert(
                "Posiciones reales sin protección",
                f"Se restauraron {len(self._positions)} posiciones reales, pero "
                "ENABLE_REAL_TRADING/EXECUTION_MODE no están activos. El guardián no "
                "puede cerrarlas hasta habilitar el trading real.",
                AlertSeverity.CRITICAL,
                {"positions": len(self._positions)},
            )
            return

        missing = sorted(
            {
                position.exchange.value
                for position in self._positions.values()
                if self.vault_service.vault.get_credential_summary(position.exchange.value) is None
            }
        )
        if missing:
            await self._alert(
                "Posiciones reales sin credenciales",
                "Se restauraron posiciones reales, pero faltan credenciales en el vault "
                f"para: {', '.join(missing)}. El guardián no puede cerrarlas.",
                AlertSeverity.CRITICAL,
                {"exchanges": missing, "positions": len(self._positions)},
            )

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def account_balances(self, exchange: Exchange | str | None = None) -> dict[str, float]:
        exchange_id = self._exchange(exchange)
        self._credentials(exchange_id)
        adapter = self._adapter(exchange_id)
        try:
            balances = await adapter.fetch_balances()
        finally:
            await adapter.close()
        self._usdt_balance = Decimal(str(balances.get("USDT", 0.0)))
        return balances

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def place_order(
        self,
        req: PlaceOrderRequest,
        current_market_price: float,
        confirm: bool = False,
        exchange: Exchange | str | None = None,
    ) -> Order:
        """Validate and submit a real order, then update the in-memory ledger."""
        exchange_id = self._exchange(exchange)
        symbol = req.symbol.replace("-", "/").replace("_", "/").upper()
        notional = Decimal(str(current_market_price)) * Decimal(str(req.quantity))
        client_order_id = req.client_order_id or f"ata-{uuid4().hex[:24]}"
        if len(client_order_id) > 36:
            raise ValidationError("client_order_id must be at most 36 characters.")

        self._assert_can_trade(exchange_id, confirm)

        # Idempotent retry: the same client order id never reaches the exchange twice.
        if req.client_order_id:
            existing = self._client_order_ids.get(req.client_order_id)
            if existing is not None:
                logger.info(
                    "live_order_idempotent_hit",
                    client_order_id=req.client_order_id,
                    order_id=str(existing.id),
                )
                return existing

        self._assert_order_allowed(symbol, notional)

        async with self._lock:
            existing = self._client_order_ids.get(client_order_id)
            if existing is not None:
                return existing
            adapter = self._adapter(exchange_id)
            try:
                balances = await adapter.fetch_balances()
                usdt = Decimal(str(balances.get("USDT", 0.0)))
                self._usdt_balance = usdt

                if self.risk_service is not None:
                    risk_request = OrderRiskCheckRequest(
                        symbol=symbol,
                        side=req.side.value,
                        order_type=req.order_type.value,
                        price=current_market_price,
                        quantity=req.quantity,
                        stop_loss=req.stop_loss,
                        take_profit=req.take_profit,
                        account_balance=float(usdt),
                        current_open_positions_count=len(self._positions),
                        current_daily_loss_pct=self.risk_service.current_daily_loss_pct,
                        current_drawdown_pct=float(self.get_portfolio().current_drawdown_pct),
                    )
                    result = self.risk_service.validate_order(risk_request)
                    if not result.is_allowed:
                        reason = "; ".join(result.violation_reasons)
                        raise RiskViolationError(f"Order rejected by Risk Guard: {reason}")

                order = await adapter.place_order(
                    symbol=symbol,
                    side=req.side,
                    order_type=req.order_type,
                    quantity=req.quantity,
                    price=req.price if req.order_type != OrderType.MARKET else None,
                    client_order_id=client_order_id,
                )
            finally:
                await adapter.close()

        order = order.model_copy(
            update={
                "stop_loss": Decimal(str(req.stop_loss)) if req.stop_loss else None,
                "take_profit": Decimal(str(req.take_profit)) if req.take_profit else None,
                "strategy_id": req.strategy_id,
                "client_order_id": order.client_order_id or client_order_id,
                "notes": req.notes or order.notes,
            }
        )
        self._record(order)

        severity = (
            AlertSeverity.SUCCESS if order.status == OrderStatus.FILLED else AlertSeverity.INFO
        )
        await self._alert(
            "Orden real ejecutada" if order.status == OrderStatus.FILLED else "Orden real enviada",
            f"{order.side.value.upper()} {order.quantity} {order.symbol} "
            f"({order.order_type.value}) en {exchange_id.value} — estado: {order.status.value}.",
            severity,
            {"order_id": str(order.id), "symbol": order.symbol, "exchange": exchange_id.value},
        )
        return order

    async def close_position(
        self,
        position_id: UUID,
        current_market_price: float,
        mode: ExecutionMode = ExecutionMode.REAL,
        confirm: bool = True,
        exchange: Exchange | str | None = None,
    ) -> Order | None:
        """Close a tracked long position with a market sell."""
        position = next((p for p in self._positions.values() if p.id == position_id), None)
        if position is None or position.id in self._closing:
            return None
        self._closing.add(position.id)
        try:
            return await self._market_sell(
                symbol=position.symbol,
                quantity=float(position.quantity),
                current_market_price=current_market_price,
                exchange=exchange or position.exchange,
                confirm=confirm,
                reason="Cierre de posición",
            )
        finally:
            self._closing.discard(position.id)

    async def close_symbol(
        self,
        symbol: str,
        quantity: float | None = None,
        current_market_price: float = 0.0,
        confirm: bool = False,
        exchange: Exchange | str | None = None,
    ) -> Order:
        """Close a symbol by selling free base-asset balance (spot semantics)."""
        exchange_id = self._exchange(exchange)
        normalized = symbol.replace("-", "/").replace("_", "/").upper()
        self._assert_can_trade(exchange_id, confirm)

        if quantity is None or quantity <= 0:
            base = normalized.split("/")[0]
            adapter = self._adapter(exchange_id)
            try:
                quantity = await adapter.fetch_free_balance(base)
            finally:
                await adapter.close()
            if quantity <= 0:
                raise ValidationError(f"No free {base} balance to close for {normalized}.")

        return await self._market_sell(
            symbol=normalized,
            quantity=quantity,
            current_market_price=current_market_price,
            exchange=exchange_id,
            confirm=confirm,
            reason="Cierre manual por símbolo",
        )

    async def _market_sell(
        self,
        symbol: str,
        quantity: float,
        current_market_price: float,
        exchange: Exchange | str | None,
        confirm: bool,
        reason: str,
    ) -> Order:
        exchange_id = self._exchange(exchange)
        self._assert_can_trade(exchange_id, confirm)
        async with self._lock:
            adapter = self._adapter(exchange_id)
            try:
                order = await adapter.place_order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                    price=None,
                )
            finally:
                await adapter.close()

        self._record(order)
        await self._alert(
            "Posición real cerrada",
            f"{reason}: SELL {quantity} {symbol} en {exchange_id.value} "
            f"@ ${current_market_price:,.2f}.",
            AlertSeverity.WARNING,
            {"order_id": str(order.id), "symbol": symbol, "exchange": exchange_id.value},
        )
        return order

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    def _record(self, order: Order) -> None:
        if order.id in self._order_ids:
            return
        self._order_ids.add(order.id)
        self._orders.append(order)
        if order.client_order_id:
            self._client_order_ids[order.client_order_id] = order
        if order.status != OrderStatus.FILLED:
            return

        fill_price = order.average_fill_price
        if fill_price is None:
            return

        self._trades.append(
            Trade(
                order_id=order.id,
                symbol=order.symbol,
                exchange=order.exchange,
                mode=ExecutionMode.REAL,
                side=order.side,
                quantity=order.filled_quantity or order.quantity,
                price=fill_price,
                commission=order.commission,
                commission_asset=order.commission_asset,
                strategy_id=order.strategy_id,
            )
        )

        notional = fill_price * (order.filled_quantity or order.quantity)
        if self._usdt_balance is not None:
            if order.side == OrderSide.BUY:
                self._usdt_balance -= notional + order.commission
            else:
                self._usdt_balance += notional - order.commission

        existing = self._positions.get(order.symbol)
        if order.side == OrderSide.BUY:
            if existing is None:
                self._positions[order.symbol] = Position(
                    symbol=order.symbol,
                    exchange=order.exchange,
                    mode=ExecutionMode.REAL,
                    side=PositionSide.LONG,
                    entry_price=fill_price,
                    quantity=order.filled_quantity or order.quantity,
                    current_price=fill_price,
                    total_commission=order.commission,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                )
            else:
                total_qty = existing.quantity + (order.filled_quantity or order.quantity)
                weighted = (
                    existing.entry_price * existing.quantity
                    + fill_price * (order.filled_quantity or order.quantity)
                ) / total_qty
                existing.entry_price = weighted
                existing.quantity = total_qty
                existing.total_commission += order.commission
                existing.stop_loss = order.stop_loss or existing.stop_loss
                existing.take_profit = order.take_profit or existing.take_profit
                existing.updated_at = datetime.now(tz=UTC)
        elif existing is not None:
            sold = order.filled_quantity or order.quantity
            self._realized_pnl += (fill_price - existing.entry_price) * sold - order.commission
            existing.quantity -= sold
            if existing.quantity <= Decimal("0.00000001"):
                del self._positions[order.symbol]
            else:
                existing.updated_at = datetime.now(tz=UTC)

    def update_market_prices(
        self,
        price_map: dict[str, float],
        mode: ExecutionMode = ExecutionMode.REAL,
    ) -> None:
        for symbol, price in price_map.items():
            position = self._positions.get(symbol)
            if position is not None:
                position.current_price = Decimal(str(price))
                position.updated_at = datetime.now(tz=UTC)

    def get_orders(self, mode: ExecutionMode = ExecutionMode.REAL) -> list[Order]:
        return [order for order in self._orders if order.mode == mode]

    def get_trades(self, mode: ExecutionMode = ExecutionMode.REAL) -> list[Trade]:
        return [trade for trade in self._trades if trade.mode == mode]

    def get_positions(self, mode: ExecutionMode = ExecutionMode.REAL) -> list[Position]:
        return [position for position in self._positions.values() if position.mode == mode]

    def get_portfolio(self, mode: ExecutionMode = ExecutionMode.REAL) -> Portfolio:
        positions = self.get_positions(mode)
        usdt = self._usdt_balance or Decimal("0")
        positions_value = sum(
            (position.quantity * position.current_price for position in positions),
            Decimal("0"),
        )
        total = usdt + positions_value
        self._peak_value = max(self._peak_value, total)
        return Portfolio(
            mode=mode,
            exchange=self.get_positions(mode)[0].exchange
            if positions
            else self._exchange(self.settings.real_trading_exchange),
            total_balance=total,
            available_balance=usdt,
            locked_balance=Decimal("0"),
            positions=positions,
            total_realized_pnl=self._realized_pnl,
            peak_value=self._peak_value,
        )

    async def reconcile_positions(self) -> list[str]:
        """
        Best-effort reconciliation of tracked positions with exchange balances.

        Spot positions are removed when the base asset is gone (closed manually
        or by an exchange-side order) and reduced when the balance is lower than
        the tracked quantity. Network/vault errors are logged and never raise.
        """
        if not self._positions or not self.settings.real_trading_enabled:
            return []

        by_exchange: dict[Exchange, list[Position]] = {}
        for position in self._positions.values():
            by_exchange.setdefault(position.exchange, []).append(position)

        removed: list[str] = []
        changed = False
        for exchange_id, positions in by_exchange.items():
            if self.vault_service.vault.get_credential_summary(exchange_id.value) is None:
                continue
            adapter: LiveExchangeAdapter | None = None
            try:
                adapter = self._adapter(exchange_id)
                balances = await adapter.fetch_balances()
            except Exception as exc:
                logger.warning(
                    "live_position_reconcile_failed",
                    exchange=exchange_id.value,
                    error=str(exc),
                )
                continue
            finally:
                if adapter is not None:
                    await adapter.close()

            for position in positions:
                base = position.symbol.split("/")[0]
                actual = Decimal(str(balances.get(base, 0.0)))
                if actual <= Decimal("0.00000001"):
                    self._positions.pop(position.symbol, None)
                    removed.append(position.symbol)
                    changed = True
                    await self._alert(
                        "Posición real cerrada fuera de la plataforma",
                        f"{position.symbol} ya no tiene saldo en {exchange_id.value}; "
                        "se eliminó del ledger local. Revisa el historial del exchange.",
                        AlertSeverity.WARNING,
                        {"symbol": position.symbol, "exchange": exchange_id.value},
                    )
                elif actual < position.quantity:
                    previous = position.quantity
                    position.quantity = actual
                    position.updated_at = datetime.now(tz=UTC)
                    changed = True
                    await self._alert(
                        "Posición real ajustada",
                        f"{position.symbol}: cantidad local {previous} ajustada a {actual} "
                        f"según el saldo de {exchange_id.value}.",
                        AlertSeverity.WARNING,
                        {"symbol": position.symbol, "exchange": exchange_id.value},
                    )
        if changed and self._on_reconcile is not None:
            try:
                await self._on_reconcile()
            except Exception as exc:
                logger.warning("live_reconcile_persist_failed", error=str(exc))
        return removed

    # ------------------------------------------------------------------
    # Protective exits (local SL/TP monitor)
    # ------------------------------------------------------------------

    async def check_protective_exits(self, price_map: dict[str, float]) -> list[Order]:
        """Close tracked positions whose SL/TP has been crossed."""
        self.update_market_prices(price_map)
        exits: list[Order] = []
        for position in list(self._positions.values()):
            price = price_map.get(position.symbol)
            if price is None:
                continue
            triggered_reason: str | None = None
            if position.stop_loss is not None and price <= float(position.stop_loss):
                triggered_reason = "Stop-Loss"
            elif position.take_profit is not None and price >= float(position.take_profit):
                triggered_reason = "Take-Profit"
            if triggered_reason is None:
                continue

            try:
                order = await self.close_position(
                    position_id=position.id,
                    current_market_price=price,
                    confirm=True,
                    exchange=position.exchange,
                )
            except Exception as exc:
                logger.warning(
                    "live_protective_exit_failed",
                    symbol=position.symbol,
                    reason=triggered_reason,
                    error=str(exc),
                )
                await self._alert(
                    "Fallo al ejecutar protección real",
                    f"{triggered_reason} de {position.symbol} @ ${price:,.2f}: "
                    f"no se pudo cerrar la posición ({exc}).",
                    AlertSeverity.CRITICAL,
                    {"symbol": position.symbol, "reason": triggered_reason},
                )
                continue
            if order is not None:
                exits.append(order)
                await self._alert(
                    f"{triggered_reason} real activado",
                    f"{triggered_reason} de {position.symbol} @ ${price:,.2f}. "
                    "Posición cerrada con orden de mercado.",
                    AlertSeverity.CRITICAL,
                    {"symbol": position.symbol, "order_id": str(order.id)},
                )
                if self._on_execution is not None:
                    await self._on_execution(order)
        return exits

    def start_guard(
        self,
        price_fetcher: PriceFetcher,
        on_execution: ExecutionCallback | None = None,
        on_reconcile: ReconcileCallback | None = None,
    ) -> None:
        """Start the background SL/TP monitor (idempotent)."""
        self._price_fetcher = price_fetcher
        self._on_execution = on_execution
        self._on_reconcile = on_reconcile
        if self._guard_task is None or self._guard_task.done():
            self._guard_task = asyncio.create_task(self._guard_loop(), name="live-trading-guard")

    async def stop_guard(self) -> None:
        task = self._guard_task
        self._guard_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _guard_loop(self) -> None:
        interval = self.settings.real_trading_guard_interval_seconds
        while True:
            try:
                if self._positions and self.settings.real_trading_enabled:
                    now = time.monotonic()
                    if (
                        now - self._last_reconcile
                        >= self.settings.real_trading_reconcile_interval_seconds
                    ):
                        self._last_reconcile = now
                        await self.reconcile_positions()

                if (
                    self._positions
                    and self.settings.real_trading_enabled
                    and self._price_fetcher is not None
                ):
                    symbols = list(self._positions)
                    prices: dict[str, float] = {}
                    for symbol in symbols:
                        try:
                            price = await self._price_fetcher(symbol)
                            if price is not None:
                                prices[symbol] = price
                        except Exception as exc:
                            logger.warning("live_guard_price_failed", symbol=symbol, error=str(exc))
                    if prices:
                        await self.check_protective_exits(prices)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("live_guard_cycle_failed", error=str(exc))
            await asyncio.sleep(interval)

    async def _alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        metadata: dict[str, object],
    ) -> None:
        if self.alert_service is None:
            return
        try:
            await self.alert_service.notify(title, message, severity, metadata)
        except Exception as exc:  # pragma: no cover - alerting is best-effort
            logger.warning("live_alert_failed", error=str(exc))

    async def ensure_exchange_ready(
        self, exchange: Exchange | str | None = None
    ) -> dict[str, object]:
        """Ping the exchange and return latency/balance information."""
        exchange_id = self._exchange(exchange)
        self._credentials(exchange_id)
        adapter = self._adapter(exchange_id)
        try:
            ping = await adapter.ping()
            balances = await adapter.fetch_balances()
            return {**ping, "balances": balances}
        except Exception as exc:
            if isinstance(exc, ExchangeError):
                raise
            raise ExchangeError(f"Exchange readiness check failed: {exc}") from exc
        finally:
            await adapter.close()
