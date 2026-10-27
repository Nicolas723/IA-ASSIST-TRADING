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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

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
    environment: str = "testnet"
    exchange: str
    testnet: bool
    mainnet_allowed: bool
    credentials_configured: bool
    allowed_symbols: list[str]
    max_notional: Decimal
    require_manual_confirmation: bool
    autonomous_real_enabled: bool
    available_environments: list[str] = Field(default_factory=list)
    open_positions: int = 0
    guard_running: bool = False
    protection_mode: str = "local_guard"
    reconciliation_interval_seconds: int = 300
    oco_enabled: bool = False
    oco_protected_positions: int = 0
    oco_unprotected_positions: int = 0
    oco_discovery_failed_exchanges: list[str] = Field(default_factory=list)


class OcoProtection(BaseModel):
    """An exchange-side OCO protecting a long spot position."""

    symbol: str
    exchange: Exchange
    order_list_id: str
    list_client_order_id: str | None = None
    quantity: Decimal
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    stop_limit_price: Decimal | None = None
    coordinator_decision_id: UUID | None = None
    status: str = "EXECUTING"
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class LiveTradingService:
    """Execute real orders through CCXT while enforcing platform safeguards."""

    # Every order created by this service carries this prefix. It is the only
    # safe basis for adopting exchange-side orders during startup discovery.
    OWN_CLIENT_ORDER_PREFIX = "ata-"

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

        setter = getattr(self.vault_service, "set_active_environment", None)
        if callable(setter):
            setter(self.settings.trading_environment.value)

        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._trades: list[Trade] = []
        self._usdt_balance: Decimal | None = None
        self._realized_pnl = Decimal("0")
        self._peak_value = Decimal("0")
        self._daily_start_equity = Decimal("0")
        self._daily_reference_date: date | None = datetime.now(tz=UTC).date()
        self._lock = asyncio.Lock()
        self._closing: set[UUID] = set()
        self._order_ids: set[UUID] = set()
        self._client_order_ids: dict[str, Order] = {}
        self._last_reconcile = 0.0
        self._last_reconcile_ok: bool | None = None
        self._last_reconcile_error: str | None = None
        self._last_reconcile_at: datetime | None = None
        self._oco_protections: dict[str, OcoProtection] = {}
        self._oco_discovery_failed: set[Exchange] = set()
        self._last_oco_ensure = 0.0

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
        environment = self.settings.trading_environment.value
        summary = self.vault_service.vault.get_credential_summary(exchange_id.value)
        available_environments: list[str] = []
        env_lister = getattr(self.vault_service.vault, "list_environments", None)
        if callable(env_lister):
            try:
                available_environments = list(env_lister(exchange_id.value))
            except Exception:
                available_environments = []
        oco_enabled = self.settings.real_trading_oco_enabled and exchange_id == Exchange.BINANCE
        if not oco_enabled:
            protection_mode = "local_guard"
        elif self._oco_discovery_failed:
            protection_mode = "oco_discovery_failed"
        elif self._oco_protections:
            protection_mode = "exchange_oco"
        else:
            protection_mode = "oco_with_local_fallback"
        oco_unprotected = (
            sum(
                1
                for position in self._positions.values()
                if position.exchange == Exchange.BINANCE
                and position.quantity > 0
                and position.symbol not in self._oco_protections
            )
            if oco_enabled
            else 0
        )
        return LiveTradingStatus(
            enabled=self.settings.real_trading_enabled,
            execution_mode=self.settings.execution_mode.value,
            environment=environment,
            exchange=exchange_id.value,
            testnet=(summary.is_testnet if summary else environment == "testnet"),
            mainnet_allowed=self.settings.allow_mainnet_trading,
            credentials_configured=summary is not None,
            allowed_symbols=list(self.settings.real_trading_allowed_symbols),
            max_notional=self.settings.real_trading_max_notional,
            require_manual_confirmation=self.settings.require_manual_confirmation,
            autonomous_real_enabled=self.settings.enable_autonomous_real_trading,
            available_environments=available_environments,
            open_positions=len(self._positions),
            guard_running=self._guard_task is not None and not self._guard_task.done(),
            protection_mode=protection_mode,
            reconciliation_interval_seconds=(self.settings.real_trading_reconcile_interval_seconds),
            oco_enabled=oco_enabled,
            oco_protected_positions=len(self._oco_protections),
            oco_unprotected_positions=oco_unprotected,
            oco_discovery_failed_exchanges=sorted(
                item.value for item in self._oco_discovery_failed
            ),
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
                "Real trading is disabled. Actívalo en Configuración > Control de ejecución."
            )
        summary = self.vault_service.vault.get_credential_summary(exchange.value)
        if summary is None:
            environment = self.settings.trading_environment.value
            raise SecurityError(
                f"No hay credenciales {environment} configuradas para {exchange.value}."
            )
        if not summary.is_testnet and not self.settings.allow_mainnet_trading:
            raise SecurityError(
                "Mainnet seleccionado. Activa la segunda confirmación "
                "(ALLOW_MAINNET_TRADING) en Configuración > Control de ejecución."
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

    def _assert_oco_discovery_known(self, exchange_id: Exchange) -> None:
        """Block sells while an unknown exchange-side OCO may still be active."""
        if self.settings.real_trading_oco_enabled and exchange_id in self._oco_discovery_failed:
            raise SecurityError(
                "No se puede vender o cerrar: no se pudieron descubrir las OCO abiertas "
                f"en {exchange_id.value}. Ejecuta la reconciliación y revisa el exchange "
                "antes de reintentar, para evitar una doble venta."
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
        daily_pnl: Decimal | None = None,
        snapshot_equity: Decimal | None = None,
        snapshot_updated_at: datetime | None = None,
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
        today = datetime.now(tz=UTC).date()
        if snapshot_updated_at is not None and snapshot_updated_at.date() == today:
            self._daily_reference_date = today
            self._daily_start_equity = (snapshot_equity or self._usdt_balance or Decimal("0")) - (
                daily_pnl or Decimal("0")
            )
        else:
            self._daily_reference_date = None
        self._last_reconcile = 0.0
        self._oco_protections = {}
        self._oco_discovery_failed = set()

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
    # Exchange-side OCO protection (Binance spot)
    # ------------------------------------------------------------------

    @staticmethod
    def _oco_status(data: dict[str, Any] | None) -> str:
        if not data:
            return ""
        list_status = data.get("listOrderStatus")
        if list_status:
            return str(list_status).upper()
        list_type = str(data.get("listStatusType") or "").upper()
        if list_type in {"EXEC_STARTED", "RESPONSE"}:
            return "EXECUTING"
        if list_type:
            return list_type
        return str(data.get("status") or "").upper()

    def _remove_oco(self, protection: OcoProtection) -> None:
        current = self._oco_protections.get(protection.symbol)
        if current is not None and current.order_list_id == protection.order_list_id:
            self._oco_protections.pop(protection.symbol, None)

    def _oco_exit_order(self, protection: OcoProtection, data: dict[str, Any]) -> Order | None:
        """Build the synthetic sell order for an executed OCO leg."""
        legs = data.get("orders") or []
        filled: list[dict[str, Any]] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            quantity = Decimal(str(leg.get("executedQty") or leg.get("executedQuantity") or 0))
            status = str(leg.get("status") or "").upper()
            if quantity > 0 and status in {"FILLED", "PARTIALLY_FILLED"}:
                filled.append(leg)
        if not filled:
            return None

        leg = max(
            filled,
            key=lambda item: Decimal(
                str(item.get("executedQty") or item.get("executedQuantity") or 0)
            ),
        )
        quantity = Decimal(str(leg.get("executedQty") or leg.get("executedQuantity") or 0))
        quote = Decimal(str(leg.get("cummulativeQuoteQty") or leg.get("cumulativeQuoteQty") or 0))
        average = (
            quote / quantity if quantity > 0 and quote > 0 else Decimal(str(leg.get("price") or 0))
        )
        if quantity <= 0 or average <= 0:
            return None

        leg_type = str(leg.get("type") or "").upper()
        is_stop = "STOP" in leg_type
        now = datetime.now(tz=UTC)
        return Order(
            symbol=protection.symbol,
            exchange=protection.exchange,
            mode=ExecutionMode.REAL,
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LIMIT if is_stop else OrderType.LIMIT,
            quantity=quantity,
            limit_price=average,
            stop_price=(protection.stop_loss or average) if is_stop else None,
            status=OrderStatus.FILLED,
            filled_quantity=quantity,
            average_fill_price=average,
            commission=Decimal("0"),
            commission_asset="USDT",
            client_order_id=protection.list_client_order_id,
            coordinator_decision_id=protection.coordinator_decision_id,
            submitted_at=now,
            filled_at=now,
            notes=f"Binance OCO {protection.order_list_id} executed ({leg_type or 'LEG'})",
        )

    async def _handle_terminal_oco(
        self, protection: OcoProtection, data: dict[str, Any] | None
    ) -> Order | None:
        """Process an OCO that is no longer EXECUTING."""
        status = self._oco_status(data)
        protection.status = status or protection.status
        self._remove_oco(protection)
        if status == "ALL_DONE" and data is not None:
            order = self._oco_exit_order(protection, data)
            if order is not None:
                self._record(order)
                await self._alert(
                    "OCO real ejecutado",
                    f"{protection.symbol}: la OCO {protection.order_list_id} se ejecutó. "
                    f"Salida registrada @ ${order.average_fill_price:,.2f}.",
                    AlertSeverity.SUCCESS,
                    {
                        "symbol": protection.symbol,
                        "order_list_id": protection.order_list_id,
                        "order_id": str(order.id),
                    },
                )
                if self._on_execution is not None:
                    await self._on_execution(order)
                return order
            await self._alert(
                "OCO finalizado sin fill",
                f"{protection.symbol}: la OCO {protection.order_list_id} terminó sin una "
                "ejecución detectable. Revisa el exchange.",
                AlertSeverity.WARNING,
                {
                    "symbol": protection.symbol,
                    "order_list_id": protection.order_list_id,
                },
            )
            return None
        await self._alert(
            "OCO cancelada o expirada",
            f"{protection.symbol}: la OCO {protection.order_list_id} ya no está activa "
            f"(estado: {status or 'desconocido'}). El guardián local vuelve a proteger "
            "la posición.",
            AlertSeverity.WARNING,
            {"symbol": protection.symbol, "order_list_id": protection.order_list_id},
        )
        return None

    async def _protect_position_with_oco(self, order: Order) -> None:
        """Create a Binance OCO for a filled long entry. Best-effort by design."""
        if order.average_fill_price is None:
            return
        position = self._positions.get(order.symbol)
        if position is None:
            return
        await self._create_oco_for_position(
            position=position,
            take_profit=order.take_profit,
            stop_loss=order.stop_loss,
            coordinator_decision_id=order.coordinator_decision_id,
            reference_price=order.average_fill_price,
            source="entry",
        )

    async def _create_oco_for_position(
        self,
        *,
        position: Position,
        take_profit: Decimal | None,
        stop_loss: Decimal | None,
        coordinator_decision_id: UUID | None = None,
        reference_price: Decimal | None = None,
        source: str = "entry",
    ) -> bool:
        """Create (or replace) an exchange-side OCO for one long spot position.

        ``reference_price`` is the price the TP/SL must straddle: the fill price
        for a new entry or the current mark when protecting an existing
        position. Returning ``False`` means the local guard remains in charge.
        """
        if take_profit is None or stop_loss is None:
            return False
        reference = reference_price or position.current_price or position.entry_price
        if reference <= 0 or take_profit <= reference or stop_loss >= reference:
            await self._alert(
                "OCO no colocada",
                f"{position.symbol}: SL/TP no válidos respecto al precio de "
                f"referencia (${reference:,.2f}). Se mantiene el guardián local "
                "como protección.",
                AlertSeverity.WARNING,
                {"symbol": position.symbol, "source": source},
            )
            return False

        buffer_pct = self.settings.real_trading_oco_stop_limit_buffer_pct / Decimal("100")
        stop_limit = stop_loss * (Decimal("1") - buffer_pct)
        if stop_limit <= 0:
            stop_limit = stop_loss
        list_client_order_id = f"oco-{uuid4().hex[:24]}"
        had_existing = position.symbol in self._oco_protections
        existing_cancelled = False
        adapter = self._adapter(Exchange.BINANCE)
        try:
            if had_existing:
                exit_order = await self._cancel_oco_for_symbol(position.symbol, adapter)
                existing_cancelled = True
                if exit_order is not None:
                    refreshed = self._positions.get(position.symbol)
                    if refreshed is None:
                        return False
                    position = refreshed
            response = await adapter.create_oco_sell(
                symbol=position.symbol,
                quantity=position.quantity,
                take_profit=take_profit,
                stop_loss=stop_loss,
                stop_limit_price=stop_limit,
                list_client_order_id=list_client_order_id,
            )
        except Exception as exc:
            logger.warning(
                "live_oco_creation_failed",
                symbol=position.symbol,
                source=source,
                error=str(exc),
            )
            fallback = (
                "La OCO anterior puede seguir activa; revisa el exchange antes de operar."
                if had_existing and not existing_cancelled
                else "Se mantiene el guardián local como protección."
            )
            await self._alert(
                "OCO no colocada",
                f"{position.symbol}: no se pudo crear la OCO ({exc}). {fallback}",
                AlertSeverity.WARNING,
                {"symbol": position.symbol, "exchange": position.exchange.value},
            )
            return False
        finally:
            await adapter.close()

        order_list_id = str(response.get("orderListId") or response.get("orderListID") or "")
        if not order_list_id:
            # The exchange accepted the request but did not return an id. Treat
            # discovery as unknown so neither the local guard nor a later
            # ensure pass can create a duplicate sell before it is resolved.
            self._oco_discovery_failed.add(Exchange.BINANCE)
            await self._alert(
                "OCO sin identificador",
                f"{position.symbol}: Binance aceptó la OCO pero no devolvió orderListId. "
                "Revisa las órdenes abiertas del exchange antes de operar.",
                AlertSeverity.CRITICAL,
                {"symbol": position.symbol},
            )
            return False

        protection = OcoProtection(
            symbol=position.symbol,
            exchange=Exchange.BINANCE,
            order_list_id=order_list_id,
            list_client_order_id=list_client_order_id,
            quantity=position.quantity,
            take_profit=take_profit,
            stop_loss=stop_loss,
            stop_limit_price=stop_limit,
            coordinator_decision_id=coordinator_decision_id,
            status=self._oco_status(response) or "EXECUTING",
        )
        self._oco_protections[position.symbol] = protection
        await self._alert(
            "OCO real colocada",
            f"{position.symbol}: OCO {order_list_id} activa. TP ${take_profit:,.2f} / "
            f"SL ${stop_loss:,.2f} (stop-limit ${stop_limit:,.2f}).",
            AlertSeverity.SUCCESS,
            {
                "symbol": position.symbol,
                "order_list_id": order_list_id,
                "quantity": str(position.quantity),
                "source": source,
            },
        )
        return True

    async def ensure_oco_protections(self) -> int:
        """Attach exchange-side OCOs to open Binance positions that lack one.

        This covers positions restored after a restart or opened while OCO was
        disabled. It is safe to call repeatedly: positions already protected are
        skipped, and no attempt is made while the exchange-side OCO discovery is
        in an unknown state (a duplicate sell would be worse than no OCO).
        """
        if not (self.settings.real_trading_enabled and self.settings.real_trading_oco_enabled):
            return 0
        if Exchange.BINANCE in self._oco_discovery_failed:
            return 0

        created = 0
        for position in list(self._positions.values()):
            if position.exchange != Exchange.BINANCE or position.quantity <= 0:
                continue
            if position.symbol in self._oco_protections:
                continue
            reference = (
                position.current_price if position.current_price > 0 else position.entry_price
            )
            protected = await self._create_oco_for_position(
                position=position,
                take_profit=position.take_profit,
                stop_loss=position.stop_loss,
                coordinator_decision_id=position.coordinator_decision_id,
                reference_price=reference,
                source="ensure",
            )
            if protected:
                created += 1
        if created:
            logger.info("live_oco_ensured", created=created)
        return created

    async def _cancel_oco_for_symbol(
        self, symbol: str, adapter: LiveExchangeAdapter
    ) -> Order | None:
        """Cancel the symbol's OCO before a market sell.

        Returns an exit order when the OCO had already executed, None when it was
        safely cancelled. Raises ``ExchangeError`` when cancellation cannot be
        confirmed, so the caller aborts instead of risking a double sell.
        """
        protection = self._oco_protections.get(symbol)
        if protection is None:
            return None
        try:
            await adapter.cancel_oco(symbol, protection.order_list_id)
        except Exception as exc:
            data: dict[str, Any] | None = None
            try:
                data = await adapter.fetch_oco(symbol, protection.order_list_id)
            except Exception:
                data = None
            status = self._oco_status(data)
            if status and status != "EXECUTING":
                return await self._handle_terminal_oco(protection, data)
            raise ExchangeError(
                f"No se pudo cancelar la OCO {protection.order_list_id} de {symbol}; "
                "se aborta el cierre para evitar una doble venta."
            ) from exc

        self._remove_oco(protection)
        await self._alert(
            "OCO cancelada",
            f"{symbol}: OCO {protection.order_list_id} cancelada antes de cerrar la posición.",
            AlertSeverity.INFO,
            {"symbol": symbol, "order_list_id": protection.order_list_id},
        )
        return None

    async def sync_oco_protections(self) -> list[Order]:
        """Refresh tracked OCOs and import any exchange-side exits."""
        if not self._oco_protections:
            return []

        by_exchange: dict[Exchange, list[OcoProtection]] = {}
        for protection in self._oco_protections.values():
            by_exchange.setdefault(protection.exchange, []).append(protection)

        exits: list[Order] = []
        for exchange, protections in by_exchange.items():
            adapter: LiveExchangeAdapter | None = None
            try:
                adapter = self._adapter(exchange)
                for protection in protections:
                    try:
                        data = await adapter.fetch_oco(protection.symbol, protection.order_list_id)
                    except Exception as exc:
                        logger.warning(
                            "live_oco_status_failed",
                            symbol=protection.symbol,
                            order_list_id=protection.order_list_id,
                            error=str(exc),
                        )
                        continue
                    status = self._oco_status(data)
                    if not status:
                        logger.warning(
                            "live_oco_status_unknown",
                            symbol=protection.symbol,
                            order_list_id=protection.order_list_id,
                        )
                        continue
                    protection.status = status
                    if status == "EXECUTING":
                        continue
                    exit_order = await self._handle_terminal_oco(protection, data)
                    if exit_order is not None:
                        exits.append(exit_order)
            except Exception as exc:
                logger.warning("live_oco_sync_failed", exchange=exchange.value, error=str(exc))
            finally:
                if adapter is not None:
                    await adapter.close()
        return exits

    async def discover_oco_protections(self) -> int:
        """
        Attach open exchange-side OCOs to restored positions.

        This is the restart safety net: if the backend was down while an OCO
        stayed active, the local guard must know about it to avoid a double sell.
        """
        if not self.settings.real_trading_oco_enabled:
            return 0

        by_exchange: dict[Exchange, list[Position]] = {}
        for position in self._positions.values():
            if position.exchange == Exchange.BINANCE:
                by_exchange.setdefault(position.exchange, []).append(position)
        if not by_exchange:
            self._oco_discovery_failed.clear()
            return 0

        discovered = 0
        for exchange, positions in by_exchange.items():
            if self.vault_service.vault.get_credential_summary(exchange.value) is None:
                self._oco_discovery_failed.add(exchange)
                continue
            adapter: LiveExchangeAdapter | None = None
            try:
                adapter = self._adapter(exchange)
                open_lists = await adapter.fetch_open_oco_lists()
                by_symbol: dict[str, list[dict[str, Any]]] = {}
                for item in open_lists:
                    raw_symbol = str(item.get("symbol") or "")
                    if not raw_symbol:
                        continue
                    try:
                        symbol = await adapter.normalize_symbol(raw_symbol)
                    except Exception:
                        logger.warning("live_oco_symbol_normalize_failed", raw_symbol=raw_symbol)
                        symbol = raw_symbol.upper()
                    by_symbol.setdefault(symbol, []).append(item)
                    raw_key = raw_symbol.replace("/", "").replace("-", "").upper()
                    if raw_key != symbol:
                        # Keep a raw-symbol alias so a failed normalization can
                        # still be matched against a tracked position.
                        by_symbol.setdefault(raw_key, []).append(item)

                for position in positions:
                    raw_key = position.symbol.replace("/", "").replace("-", "").upper()
                    items = by_symbol.get(position.symbol) or by_symbol.get(raw_key, [])
                    if not items:
                        # Never drop a tracked protection just because the open-list
                        # snapshot is empty: a concurrent placement may not be visible
                        # yet. sync_oco_protections handles terminal states explicitly.
                        continue
                    if len(items) > 1:
                        raise ExchangeError(
                            f"Multiple open OCOs found for {position.symbol}; "
                            "manual review is required to avoid a double sell."
                        )
                    item = items[0]
                    order_list_id = str(item.get("orderListId") or item.get("orderListID") or "")
                    if not order_list_id:
                        raise ExchangeError(
                            f"Open OCO for {position.symbol} has no orderListId; "
                            "manual review is required to avoid a double sell."
                        )
                    self._oco_protections[position.symbol] = OcoProtection(
                        symbol=position.symbol,
                        exchange=exchange,
                        order_list_id=order_list_id,
                        list_client_order_id=item.get("listClientOrderId"),
                        quantity=position.quantity,
                        take_profit=position.take_profit,
                        stop_loss=position.stop_loss,
                        coordinator_decision_id=position.coordinator_decision_id,
                        status=self._oco_status(item) or "EXECUTING",
                    )
                    discovered += 1
                self._oco_discovery_failed.discard(exchange)
            except Exception as exc:
                logger.warning(
                    "live_oco_discovery_failed",
                    exchange=exchange.value,
                    error=str(exc),
                )
                self._oco_discovery_failed.add(exchange)
                await self._alert(
                    "No se pudieron descubrir OCO",
                    f"{exchange.value}: no se pudo consultar las OCO abiertas ({exc}). "
                    "El guardián local no cerrará posiciones de este exchange hasta "
                    "confirmar que no hay una OCO activa, para evitar una doble venta.",
                    AlertSeverity.CRITICAL,
                    {"exchange": exchange.value, "positions": len(positions)},
                )
            finally:
                if adapter is not None:
                    await adapter.close()

        if discovered:
            await self._alert(
                "OCO reanudadas",
                f"Se encontraron {discovered} OCO activas y se reanudó su seguimiento.",
                AlertSeverity.INFO,
                {"count": discovered},
            )
        return discovered

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

    async def account_details(
        self, exchange: Exchange | str | None = None
    ) -> dict[str, dict[str, float]]:
        """Return total/free/used balances for the active credential."""
        exchange_id = self._exchange(exchange)
        self._credentials(exchange_id)
        adapter = self._adapter(exchange_id)
        try:
            details = await adapter.fetch_balance_details()
        finally:
            await adapter.close()
        self._usdt_balance = Decimal(str(details.get("USDT", {}).get("total", 0.0)))
        return details

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
        client_order_id = req.client_order_id or (
            f"{self.OWN_CLIENT_ORDER_PREFIX}{uuid4().hex[:24]}"
        )
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

        if req.side == OrderSide.SELL:
            self._assert_oco_discovery_known(exchange_id)

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
                    portfolio = self.get_portfolio()
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
                        current_total_exposure_usd=float(portfolio.gross_exposure_usd),
                        current_symbol_exposure_usd=float(portfolio.exposure_usd(symbol)),
                        current_daily_loss_pct=self.risk_service.current_daily_loss_pct,
                        current_drawdown_pct=float(portfolio.current_drawdown_pct),
                    )
                    result = self.risk_service.validate_order(risk_request)
                    if not result.is_allowed:
                        reason = "; ".join(result.violation_reasons)
                        raise RiskViolationError(f"Order rejected by Risk Guard: {reason}")

                if req.side == OrderSide.SELL:
                    self._assert_oco_discovery_known(exchange_id)
                    oco_exit = await self._cancel_oco_for_symbol(symbol, adapter)
                    if oco_exit is not None:
                        self._client_order_ids[client_order_id] = oco_exit
                        return oco_exit

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
                "coordinator_decision_id": req.coordinator_decision_id
                or (
                    self._positions[symbol].coordinator_decision_id
                    if symbol in self._positions
                    else None
                ),
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

        if (
            order.status == OrderStatus.FILLED
            and order.side == OrderSide.BUY
            and order.exchange == Exchange.BINANCE
            and self.settings.real_trading_oco_enabled
            and order.stop_loss is not None
            and order.take_profit is not None
        ):
            await self._protect_position_with_oco(order)
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
        self._assert_oco_discovery_known(exchange_id)

        if quantity is None or quantity <= 0:
            base = normalized.split("/")[0]
            adapter = self._adapter(exchange_id)
            try:
                oco_exit = await self._cancel_oco_for_symbol(normalized, adapter)
                if oco_exit is not None:
                    return oco_exit
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
        self._assert_oco_discovery_known(exchange_id)
        async with self._lock:
            self._assert_oco_discovery_known(exchange_id)
            position = self._positions.get(symbol)
            adapter = self._adapter(exchange_id)
            try:
                oco_exit = await self._cancel_oco_for_symbol(symbol, adapter)
                if oco_exit is not None:
                    return oco_exit
                order = await adapter.place_order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                    price=None,
                )
            finally:
                await adapter.close()

        if order.coordinator_decision_id is None and position is not None:
            order = order.model_copy(
                update={"coordinator_decision_id": position.coordinator_decision_id}
            )
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
        if order.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return

        fill_price = order.average_fill_price
        if fill_price is None:
            return
        executed_quantity = order.filled_quantity
        if executed_quantity is None:
            if order.status == OrderStatus.PARTIALLY_FILLED:
                logger.error(
                    "live_partial_fill_missing_quantity",
                    order_id=str(order.id),
                    symbol=order.symbol,
                )
                return
            executed_quantity = order.quantity
        if executed_quantity <= 0:
            logger.warning(
                "live_fill_non_positive_quantity",
                order_id=str(order.id),
                status=order.status.value,
            )
            return

        self._apply_fill(order, executed_quantity, fill_price, order.commission)

    def _apply_fill(
        self,
        order: Order,
        executed_quantity: Decimal,
        fill_price: Decimal,
        commission: Decimal,
    ) -> None:
        """Book an executed quantity into trades, cash, and the position ledger."""
        self._trades.append(
            Trade(
                order_id=order.id,
                symbol=order.symbol,
                exchange=order.exchange,
                mode=ExecutionMode.REAL,
                side=order.side,
                quantity=executed_quantity,
                price=fill_price,
                commission=commission,
                commission_asset=order.commission_asset,
                strategy_id=order.strategy_id,
                coordinator_decision_id=order.coordinator_decision_id,
            )
        )

        notional = fill_price * executed_quantity
        if self._usdt_balance is not None:
            if order.side == OrderSide.BUY:
                self._usdt_balance -= notional + commission
            else:
                self._usdt_balance += notional - commission

        existing = self._positions.get(order.symbol)
        if order.side == OrderSide.BUY:
            if existing is None:
                self._positions[order.symbol] = Position(
                    symbol=order.symbol,
                    exchange=order.exchange,
                    mode=ExecutionMode.REAL,
                    side=PositionSide.LONG,
                    entry_price=fill_price,
                    quantity=executed_quantity,
                    current_price=fill_price,
                    total_commission=commission,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    coordinator_decision_id=order.coordinator_decision_id,
                )
            else:
                total_qty = existing.quantity + executed_quantity
                weighted = (
                    existing.entry_price * existing.quantity + fill_price * executed_quantity
                ) / total_qty
                existing.entry_price = weighted
                existing.quantity = total_qty
                existing.total_commission += commission
                existing.stop_loss = order.stop_loss or existing.stop_loss
                existing.take_profit = order.take_profit or existing.take_profit
                existing.coordinator_decision_id = (
                    existing.coordinator_decision_id or order.coordinator_decision_id
                )
                existing.updated_at = datetime.now(tz=UTC)
        elif existing is not None:
            self._realized_pnl += (
                fill_price - existing.entry_price
            ) * executed_quantity - commission
            existing.quantity -= executed_quantity
            if existing.quantity <= Decimal("0.00000001"):
                del self._positions[order.symbol]
            else:
                existing.updated_at = datetime.now(tz=UTC)

    def _apply_order_update(self, local: Order, fetched: Order) -> bool:
        """Merge an exchange order snapshot into a tracked local order.

        Only the newly executed quantity is booked, so polling the same order
        repeatedly is idempotent.
        """
        changed = False
        previous_filled = local.filled_quantity or Decimal("0")
        new_filled = fetched.filled_quantity or Decimal("0")
        if new_filled > previous_filled:
            price = fetched.average_fill_price or local.average_fill_price
            if price is None:
                logger.warning(
                    "live_reconcile_fill_without_price",
                    order_id=str(local.id),
                    symbol=local.symbol,
                )
            else:
                delta = new_filled - previous_filled
                commission = max(
                    Decimal("0"),
                    (fetched.commission or Decimal("0")) - (local.commission or Decimal("0")),
                )
                self._apply_fill(local, delta, price, commission)
                local.filled_quantity = new_filled
                local.average_fill_price = price
                local.commission = max(
                    local.commission or Decimal("0"), fetched.commission or Decimal("0")
                )
                changed = True
        if fetched.status != local.status:
            local.status = fetched.status
            changed = True
        now = datetime.now(tz=UTC)
        if local.status == OrderStatus.FILLED and local.filled_at is None:
            local.filled_at = fetched.filled_at or now
        elif local.status == OrderStatus.CANCELLED and local.cancelled_at is None:
            local.cancelled_at = fetched.cancelled_at or now
        return changed

    async def reconcile_open_orders(self) -> int:
        """Refresh open/partially-filled orders from the exchange.

        Restored orders may have kept executing while the process was down.
        Polling by client order id recovers those fills exactly once and lets
        the caller persist the merged ledger.
        """
        if not self.settings.real_trading_enabled:
            return 0
        pending = [
            order
            for order in self._orders
            if order.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)
        ]
        updated = 0
        for order in pending:
            if not order.client_order_id:
                logger.warning(
                    "live_reconcile_missing_client_order_id",
                    order_id=str(order.id),
                    symbol=order.symbol,
                )
                continue
            adapter: LiveExchangeAdapter | None = None
            try:
                adapter = self._adapter(order.exchange)
                fetched = await adapter.fetch_order(
                    order.symbol, client_order_id=order.client_order_id
                )
            except Exception as exc:
                logger.warning(
                    "live_open_order_reconcile_failed",
                    order_id=str(order.id),
                    symbol=order.symbol,
                    error=str(exc),
                )
                continue
            finally:
                if adapter is not None:
                    await adapter.close()
            if fetched is None:
                continue
            if self._apply_order_update(order, fetched):
                updated += 1
                if self._on_execution is not None:
                    try:
                        await self._on_execution(order)
                    except Exception as exc:
                        logger.warning(
                            "live_reconciled_order_persist_failed",
                            order_id=str(order.id),
                            error=str(exc),
                        )
        if updated and self._on_reconcile is not None:
            try:
                await self._on_reconcile()
            except Exception as exc:
                logger.warning("live_reconcile_persist_failed", error=str(exc))
        return updated

    async def discover_untracked_orders(self) -> int:
        """Adopt exchange open orders created by this system but absent locally.

        Attribution relies on the ``ata-`` client order id prefix, so unrelated
        manual orders are never adopted. Only still-open orders are visible; an
        order that filled completely before this call cannot be recovered here.
        """
        if not self.settings.real_trading_enabled:
            return 0
        try:
            exchange_id = self._exchange()
        except Exception as exc:
            logger.warning("live_order_discovery_unavailable", error=str(exc))
            return 0

        imported = 0
        recovered_closed = 0
        lookback = self.settings.real_trading_order_recovery_lookback_seconds
        adapter: LiveExchangeAdapter | None = None
        try:
            adapter = self._adapter(exchange_id)
            since_ms = int((datetime.now(tz=UTC) - timedelta(seconds=lookback)).timestamp() * 1000)
            for symbol in self.settings.real_trading_allowed_symbols:
                try:
                    open_orders = await adapter.fetch_open_orders(symbol)
                except Exception as exc:
                    logger.warning(
                        "live_open_order_discovery_failed",
                        symbol=symbol,
                        error=str(exc),
                    )
                    open_orders = []
                for order in open_orders:
                    client_id = order.client_order_id
                    if not client_id or not client_id.startswith(self.OWN_CLIENT_ORDER_PREFIX):
                        continue
                    if client_id in self._client_order_ids:
                        continue
                    self._record(order)
                    imported += 1
                    if self._on_execution is not None:
                        try:
                            await self._on_execution(order)
                        except Exception as exc:
                            logger.warning(
                                "live_discovered_order_persist_failed",
                                order_id=str(order.id),
                                error=str(exc),
                            )

                # Orders that filled while this process was down are no longer
                # open. Recover them from the bounded closed-order window so the
                # ledger and position view do not silently drift.
                try:
                    closed_orders = await adapter.fetch_closed_orders(
                        symbol, since_ms=since_ms, limit=50
                    )
                except Exception as exc:
                    logger.warning(
                        "live_closed_order_discovery_failed",
                        symbol=symbol,
                        error=str(exc),
                    )
                    continue
                for order in closed_orders:
                    client_id = order.client_order_id
                    if not client_id or not client_id.startswith(self.OWN_CLIENT_ORDER_PREFIX):
                        continue
                    if client_id in self._client_order_ids:
                        continue
                    self._record(order)
                    imported += 1
                    recovered_closed += 1
                    if self._on_execution is not None:
                        try:
                            await self._on_execution(order)
                        except Exception as exc:
                            logger.warning(
                                "live_recovered_order_persist_failed",
                                order_id=str(order.id),
                                error=str(exc),
                            )
        except Exception as exc:
            logger.warning("live_order_discovery_failed", error=str(exc))
            return imported
        finally:
            if adapter is not None:
                await adapter.close()
        if recovered_closed:
            logger.warning(
                "live_closed_orders_recovered",
                count=recovered_closed,
                exchange=exchange_id.value,
                lookback_seconds=lookback,
            )
        if imported:
            logger.warning(
                "live_untracked_orders_imported",
                count=imported,
                exchange=exchange_id.value,
            )
        return imported

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
        today = datetime.now(tz=UTC).date()
        if self._daily_reference_date != today:
            self._daily_reference_date = today
            self._daily_start_equity = total
        daily_pnl = total - self._daily_start_equity
        portfolio = Portfolio(
            mode=mode,
            exchange=self.get_positions(mode)[0].exchange
            if positions
            else self._exchange(self.settings.real_trading_exchange),
            total_balance=total,
            available_balance=usdt,
            locked_balance=Decimal("0"),
            positions=positions,
            total_realized_pnl=self._realized_pnl,
            daily_pnl=daily_pnl,
            peak_value=self._peak_value,
        )
        if self.risk_service is not None:
            daily_start_equity = total - daily_pnl
            self.risk_service.current_daily_loss_pct = (
                float(-daily_pnl / daily_start_equity * 100)
                if daily_pnl < 0 and daily_start_equity > 0
                else 0.0
            )
            self.risk_service.current_drawdown_pct = float(portfolio.current_drawdown_pct)
            self.risk_service.open_positions_count = portfolio.position_count
            self.risk_service.current_equity_usd = float(total)
            self.risk_service.current_gross_exposure_usd = float(portfolio.gross_exposure_usd)
        return portfolio

    async def preflight_reconciliation(self) -> tuple[bool, str | None]:
        """Read-only gate before opening new real risk.

        A balance probe is the cheapest way to prove that the exchange is
        reachable and the stored credentials are still valid. When it fails the
        local ledger cannot be trusted, so callers must suspend new entries
        until a later probe succeeds. Existing positions are never gated: exits
        must stay possible even while reconciliation is degraded.

        Returns ``(ok, error)``; ``error`` is ``None`` when the probe succeeds.
        """
        if not self.settings.real_trading_enabled:
            return True, None

        exchange_id = self._exchange()
        adapter: LiveExchangeAdapter | None = None
        try:
            adapter = self._adapter(exchange_id)
            await adapter.fetch_balances()
        except Exception as exc:
            error = str(exc)
            self._last_reconcile_ok = False
            self._last_reconcile_error = error
            self._last_reconcile_at = datetime.now(tz=UTC)
            logger.warning(
                "live_reconcile_gate_failed",
                exchange=exchange_id.value,
                error=error,
            )
            return False, error
        finally:
            if adapter is not None:
                await adapter.close()

        self._last_reconcile_ok = True
        self._last_reconcile_error = None
        self._last_reconcile_at = datetime.now(tz=UTC)
        return True, None

    async def reconcile_positions(self) -> list[str]:
        """
        Best-effort reconciliation of tracked positions with exchange balances.

        Spot positions are removed when the base asset is gone (closed manually
        or by an exchange-side order) and reduced when the balance is lower than
        the tracked quantity. Network/vault errors are logged and never raise.
        """
        if not self.settings.real_trading_enabled:
            return []

        oco_exits = (
            await self.sync_oco_protections() if self.settings.real_trading_oco_enabled else []
        )
        if not self._positions:
            if oco_exits and self._on_reconcile is not None:
                try:
                    await self._on_reconcile()
                except Exception as exc:
                    logger.warning("live_reconcile_persist_failed", error=str(exc))
            return []

        by_exchange: dict[Exchange, list[Position]] = {}
        for position in self._positions.values():
            by_exchange.setdefault(position.exchange, []).append(position)

        removed: list[str] = []
        changed = bool(oco_exits)
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
            if self.settings.real_trading_oco_enabled:
                if position.exchange in self._oco_discovery_failed:
                    # Fail-safe: an unknown OCO may be active; never risk a double sell.
                    continue
                if position.symbol in self._oco_protections:
                    continue
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
                if (
                    self._positions
                    and self.settings.real_trading_enabled
                    and self.settings.real_trading_oco_enabled
                ):
                    now_oco = time.monotonic()
                    if self._oco_discovery_failed or now_oco - self._last_oco_ensure >= max(
                        60, self.settings.real_trading_reconcile_interval_seconds
                    ):
                        self._last_oco_ensure = now_oco
                        await self.discover_oco_protections()
                        await self.ensure_oco_protections()

                if self.settings.real_trading_enabled:
                    now = time.monotonic()
                    if (
                        now - self._last_reconcile
                        >= self.settings.real_trading_reconcile_interval_seconds
                    ):
                        self._last_reconcile = now
                        if self._positions:
                            await self.reconcile_positions()
                        await self.reconcile_open_orders()

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
