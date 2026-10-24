"""Unit tests for real-trading execution, safety interlocks and alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.services.alert_service import AlertService
from src.application.services.hydration import hydrate_live_trading_service
from src.application.services.live_trading_service import LiveTradingService
from src.domain.market.entities import Exchange
from src.domain.notifications.entities import Alert, AlertSeverity
from src.domain.notifications.ports import NotificationPort
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
from src.infrastructure.exchange.paper import PlaceOrderRequest
from src.shared.config.settings import Settings
from src.shared.exceptions.domain import (
    ExchangeError,
    RiskViolationError,
    SecurityError,
    ValidationError,
)


class FakeVault:
    """Minimal vault stand-in exposing the two methods used by the service."""

    def __init__(self, is_testnet: bool = True) -> None:
        self.summary = SimpleNamespace(is_testnet=is_testnet)
        self.vault = self

    def get_credential_summary(self, exchange: str) -> Any:
        return self.summary

    def get_decrypted_credential(self, exchange: str) -> tuple[str, str, None]:
        return ("api-key", "api-secret", None)


class FakeAlert:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, AlertSeverity, dict[str, object]]] = []

    async def notify(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        self.calls.append((title, message, severity, metadata or {}))
        return True


class FakeRisk:
    current_daily_loss_pct = 0.0

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.last_request: Any = None

    def validate_order(self, request: Any) -> Any:
        self.last_request = request
        return SimpleNamespace(
            is_allowed=self.allowed,
            violation_reasons=[] if self.allowed else ["Riesgo máximo excedido"],
        )


class FakeAdapter:
    def __init__(self) -> None:
        self.closed = False
        self.free_balance = 0.5
        self.balances = {"USDT": 10_000.0, "BTC": 1.0}
        self.last_place_kwargs: dict[str, Any] = {}
        self.place_calls = 0
        self.order_status = OrderStatus.FILLED
        self.filled_quantity_override: Decimal | None = None
        self.fail_on_sell = False
        self.fail_oco_create = False
        self.fail_oco_cancel = False
        self.fail_open_oco = False
        self.oco_create_calls: list[dict[str, Any]] = []
        self.oco_cancel_calls: list[tuple[str, str]] = []
        self.oco_fetch_calls: list[tuple[str, str]] = []
        self.fetched_order: Order | None = None
        self.fetch_order_calls: list[dict[str, Any]] = []
        self.open_orders: list[Order] = []
        self.fetch_open_orders_calls: list[str | None] = []
        self.closed_orders: list[Order] = []
        self.fetch_closed_orders_calls: list[dict[str, Any]] = []
        self.oco_create_response: dict[str, Any] = {
            "orderListId": 777,
            "listOrderStatus": "EXECUTING",
            "orders": [],
        }
        self.oco_status: dict[str, Any] = {
            "orderListId": 777,
            "listOrderStatus": "EXECUTING",
            "orders": [],
        }
        self.open_oco_lists: list[dict[str, Any]] = []

    async def fetch_balances(self) -> dict[str, float]:
        return self.balances

    async def fetch_free_balance(self, asset: str) -> float:
        return self.free_balance

    async def fetch_order(
        self, symbol: str, *, client_order_id: str | None = None, order_id: str | None = None
    ) -> Order | None:
        self.fetch_order_calls.append(
            {"symbol": symbol, "client_order_id": client_order_id, "order_id": order_id}
        )
        return self.fetched_order

    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]:
        self.fetch_open_orders_calls.append(symbol)
        return list(self.open_orders)

    async def fetch_closed_orders(
        self,
        symbol: str,
        *,
        since_ms: int | None = None,
        limit: int = 50,
    ) -> list[Order]:
        self.fetch_closed_orders_calls.append(
            {"symbol": symbol, "since_ms": since_ms, "limit": limit}
        )
        return list(self.closed_orders)

    async def place_order(self, **kwargs: Any) -> Order:
        self.last_place_kwargs = kwargs
        self.place_calls += 1
        side = kwargs["side"]
        if self.fail_on_sell and side == OrderSide.SELL:
            raise RuntimeError("sell failed")
        quantity = self.filled_quantity_override or Decimal(str(kwargs["quantity"]))
        price = Decimal("50_000") if side == OrderSide.BUY else Decimal("51_000")
        return Order(
            symbol=kwargs["symbol"],
            exchange=Exchange.BINANCE,
            mode=ExecutionMode.REAL,
            side=side,
            order_type=kwargs["order_type"],
            quantity=quantity,
            status=self.order_status,
            filled_quantity=quantity,
            average_fill_price=price,
            commission=Decimal("1"),
            client_order_id=kwargs.get("client_order_id"),
            submitted_at=datetime.now(tz=UTC),
            filled_at=datetime.now(tz=UTC),
        )

    async def create_oco_sell(self, **kwargs: Any) -> dict[str, Any]:
        self.oco_create_calls.append(kwargs)
        if self.fail_oco_create:
            raise RuntimeError("oco failed")
        return self.oco_create_response

    async def cancel_oco(self, symbol: str, order_list_id: str) -> dict[str, Any]:
        self.oco_cancel_calls.append((symbol, str(order_list_id)))
        if self.fail_oco_cancel:
            raise RuntimeError("cancel failed")
        return {"orderListId": order_list_id, "listOrderStatus": "ALL_DONE"}

    async def fetch_oco(self, symbol: str, order_list_id: str) -> dict[str, Any]:
        self.oco_fetch_calls.append((symbol, str(order_list_id)))
        return self.oco_status

    async def fetch_open_oco_lists(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self.fail_open_oco:
            raise RuntimeError("open list failed")
        return self.open_oco_lists

    async def normalize_symbol(self, raw_symbol: str) -> str:
        return "BTC/USDT" if raw_symbol == "BTCUSDT" else raw_symbol

    async def close(self) -> None:
        self.closed = True


def real_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "execution_mode": "real",
        "enable_real_trading": True,
        "require_manual_confirmation": True,
        "real_trading_allowed_symbols": ["BTC/USDT"],
        "real_trading_max_notional": Decimal("10000"),
        "allow_mainnet_trading": False,
    }
    values.update(overrides)
    return Settings(**values)


def make_service(
    *,
    settings: Settings | None = None,
    vault: FakeVault | None = None,
    risk: FakeRisk | None = None,
    alerts: FakeAlert | None = None,
) -> tuple[LiveTradingService, FakeAdapter]:
    service = LiveTradingService(
        vault_service=vault or FakeVault(),
        risk_service=risk,
        alert_service=alerts,
        settings=settings or real_settings(),
    )
    adapter = FakeAdapter()
    service._adapter = lambda *_: adapter  # type: ignore[method-assign]
    return service, adapter


def buy_request(
    *,
    symbol: str = "BTC/USDT",
    quantity: float = 0.1,
    stop_loss: float | None = 49_000,
    take_profit: float | None = 55_000,
    client_order_id: str | None = None,
    coordinator_decision_id=None,
) -> PlaceOrderRequest:
    return PlaceOrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        client_order_id=client_order_id,
        coordinator_decision_id=coordinator_decision_id,
    )


def make_position(quantity: Decimal = Decimal("0.1")) -> Position:
    return Position(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.REAL,
        side=PositionSide.LONG,
        entry_price=Decimal("50000"),
        quantity=quantity,
        current_price=Decimal("50000"),
        stop_loss=Decimal("49000"),
        take_profit=Decimal("55000"),
    )


class FakeRepo:
    """Duck-typed journal used to exercise live hydration."""

    def __init__(
        self,
        positions: list[Position],
        orders: list[Order],
        trades: list[Trade],
        snapshot: Portfolio | None = None,
    ) -> None:
        self.positions = positions
        self.orders = orders
        self.trades = trades
        self.snapshot = snapshot

    async def list_positions(
        self, mode: ExecutionMode | None = None, _symbol: str | None = None
    ) -> list[Position]:
        return [p for p in self.positions if mode is None or p.mode == mode]

    async def list_orders(
        self,
        mode: ExecutionMode | None = None,
        _symbol: str | None = None,
        limit: int = 100,
    ) -> list[Order]:
        return [o for o in self.orders if mode is None or o.mode == mode][:limit]

    async def list_trades(
        self,
        mode: ExecutionMode | None = None,
        _symbol: str | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        return [t for t in self.trades if mode is None or t.mode == mode][:limit]

    async def latest_portfolio_snapshot(
        self, mode: ExecutionMode, _exchange: Exchange
    ) -> Portfolio | None:
        return self.snapshot


def test_status_reports_defaults_without_credentials() -> None:
    service = LiveTradingService(
        vault_service=SimpleNamespace(
            vault=SimpleNamespace(get_credential_summary=lambda *_: None)
        ),
        settings=Settings(_env_file=None),
    )

    status = service.status()

    assert status.enabled is False
    assert status.execution_mode == "paper"
    assert status.credentials_configured is False
    assert status.testnet is True


async def test_place_order_blocked_when_real_trading_disabled() -> None:
    service, _ = make_service(settings=Settings(_env_file=None))

    with pytest.raises(SecurityError, match="Real trading is disabled"):
        await service.place_order(buy_request(), current_market_price=50_000, confirm=True)


async def test_place_order_requires_manual_confirmation() -> None:
    service, _ = make_service()

    with pytest.raises(SecurityError, match="Manual confirmation required"):
        await service.place_order(buy_request(), current_market_price=50_000, confirm=False)


async def test_place_order_rejects_symbol_outside_allowlist() -> None:
    service, _ = make_service()

    with pytest.raises(SecurityError, match="not in REAL_TRADING_ALLOWED_SYMBOLS"):
        await service.place_order(
            buy_request(symbol="ETH/USDT"),
            current_market_price=2_000,
            confirm=True,
        )


async def test_place_order_rejects_notional_above_limit() -> None:
    service, _ = make_service(settings=real_settings(real_trading_max_notional=Decimal("100")))

    with pytest.raises(SecurityError, match="exceeds REAL_TRADING_MAX_NOTIONAL"):
        await service.place_order(buy_request(), current_market_price=50_000, confirm=True)


async def test_place_order_rejects_mainnet_without_second_opt_in() -> None:
    service, _ = make_service(vault=FakeVault(is_testnet=False))

    with pytest.raises(SecurityError, match="ALLOW_MAINNET_TRADING"):
        await service.place_order(buy_request(), current_market_price=50_000, confirm=True)


async def test_place_order_allows_mainnet_when_explicitly_enabled() -> None:
    service, _ = make_service(
        settings=real_settings(allow_mainnet_trading=True),
        vault=FakeVault(is_testnet=False),
    )

    order = await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert order.status == OrderStatus.FILLED
    assert service.status().testnet is False


async def test_place_order_records_position_trade_and_alert() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(alerts=alerts)

    order = await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert adapter.closed is True
    assert order.stop_loss == Decimal("49000.0")
    assert order.take_profit == Decimal("55000.0")
    assert len(service.get_positions()) == 1
    assert len(service.get_trades()) == 1
    assert len(service.get_orders()) == 1
    assert service.get_portfolio().total_balance == Decimal("9999.00")
    assert alerts.calls
    assert alerts.calls[0][2] == AlertSeverity.SUCCESS


async def test_partially_filled_buy_is_tracked_with_filled_quantity() -> None:
    service, adapter = make_service()
    adapter.order_status = OrderStatus.PARTIALLY_FILLED
    adapter.filled_quantity_override = Decimal("0.04")

    order = await service.place_order(
        buy_request(quantity=0.1), current_market_price=50_000, confirm=True
    )

    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert len(service.get_positions()) == 1
    assert service.get_positions()[0].quantity == Decimal("0.04")
    assert service.get_positions()[0].stop_loss == Decimal("49000.0")
    assert len(service.get_trades()) == 1
    assert service.get_trades()[0].quantity == Decimal("0.04")
    assert adapter.closed


def make_fetched_order(
    *,
    status: OrderStatus,
    filled: Decimal,
    price: Decimal,
    commission: Decimal,
    client_order_id: str = "ata-restart",
    quantity: Decimal = Decimal("0.1"),
) -> Order:
    return Order(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.REAL,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        status=status,
        filled_quantity=filled,
        average_fill_price=price,
        commission=commission,
        client_order_id=client_order_id,
    )


async def test_restored_open_order_books_only_new_fills_once() -> None:
    service, adapter = make_service()
    local = Order(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.REAL,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        status=OrderStatus.OPEN,
        client_order_id="ata-restart",
        stop_loss=Decimal("49000"),
        take_profit=Decimal("55000"),
    )
    service.restore(positions=[], orders=[local], trades=[])
    service._on_reconcile = AsyncMock()  # type: ignore[method-assign]

    adapter.fetched_order = make_fetched_order(
        status=OrderStatus.PARTIALLY_FILLED,
        filled=Decimal("0.04"),
        price=Decimal("50000"),
        commission=Decimal("0.4"),
    )
    assert await service.reconcile_open_orders() == 1

    assert service.get_orders()[0].status == OrderStatus.PARTIALLY_FILLED
    assert service.get_positions()[0].quantity == Decimal("0.04")
    assert [trade.quantity for trade in service.get_trades()] == [Decimal("0.04")]

    adapter.fetched_order = make_fetched_order(
        status=OrderStatus.FILLED,
        filled=Decimal("0.1"),
        price=Decimal("50000"),
        commission=Decimal("1.0"),
    )
    assert await service.reconcile_open_orders() == 1

    assert service.get_orders()[0].status == OrderStatus.FILLED
    assert service.get_positions()[0].quantity == Decimal("0.1")
    assert [trade.quantity for trade in service.get_trades()] == [
        Decimal("0.04"),
        Decimal("0.06"),
    ]
    assert service._on_reconcile.await_count == 2

    # A third poll has nothing left to reconcile and must not duplicate fills.
    assert await service.reconcile_open_orders() == 0
    assert len(service.get_trades()) == 2


async def test_open_order_without_client_id_is_not_reconciled_by_guess() -> None:
    service, adapter = make_service()
    local = Order(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.REAL,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        status=OrderStatus.OPEN,
    )
    service.restore(positions=[], orders=[local], trades=[])
    adapter.fetched_order = make_fetched_order(
        status=OrderStatus.FILLED,
        filled=Decimal("0.1"),
        price=Decimal("50000"),
        commission=Decimal("1"),
        client_order_id="",
    )

    assert await service.reconcile_open_orders() == 0
    assert service.get_orders()[0].status == OrderStatus.OPEN
    assert service.get_positions() == []


async def test_discovery_adopts_only_own_untracked_open_orders() -> None:
    service, adapter = make_service()
    service._on_execution = AsyncMock()  # type: ignore[method-assign]
    own = make_fetched_order(
        status=OrderStatus.PARTIALLY_FILLED,
        filled=Decimal("0.04"),
        price=Decimal("50000"),
        commission=Decimal("0.4"),
        client_order_id="ata-crash-window",
    )
    foreign = make_fetched_order(
        status=OrderStatus.OPEN,
        filled=Decimal("0"),
        price=Decimal("50000"),
        commission=Decimal("0"),
        client_order_id="manual-order",
    )
    tracked = make_fetched_order(
        status=OrderStatus.OPEN,
        filled=Decimal("0"),
        price=Decimal("50000"),
        commission=Decimal("0"),
        client_order_id="ata-already-known",
    )
    service.restore(positions=[], orders=[tracked], trades=[])
    adapter.open_orders = [own, foreign, tracked]

    assert await service.discover_untracked_orders() == 1

    assert [order.client_order_id for order in service.get_orders()] == [
        "ata-already-known",
        "ata-crash-window",
    ]
    assert service.get_positions()[0].quantity == Decimal("0.04")
    assert [trade.quantity for trade in service.get_trades()] == [Decimal("0.04")]
    service._on_execution.assert_awaited_once()
    assert adapter.closed


async def test_discovery_is_idempotent_across_restarts() -> None:
    service, adapter = make_service()
    own = make_fetched_order(
        status=OrderStatus.OPEN,
        filled=Decimal("0"),
        price=Decimal("50000"),
        commission=Decimal("0"),
        client_order_id="ata-crash-window",
    )
    adapter.open_orders = [own]

    assert await service.discover_untracked_orders() == 1
    assert await service.discover_untracked_orders() == 0
    assert len(service.get_orders()) == 1


async def test_discovery_recovers_closed_own_orders_once() -> None:
    service, adapter = make_service()
    service._on_execution = AsyncMock()  # type: ignore[method-assign]
    recovered = make_fetched_order(
        status=OrderStatus.FILLED,
        filled=Decimal("0.05"),
        price=Decimal("50000"),
        commission=Decimal("0.5"),
        client_order_id="ata-filled-while-down",
    )
    foreign = make_fetched_order(
        status=OrderStatus.FILLED,
        filled=Decimal("0.02"),
        price=Decimal("50000"),
        commission=Decimal("0.2"),
        client_order_id="manual-filled",
    )
    adapter.closed_orders = [recovered, foreign]

    assert await service.discover_untracked_orders() == 1

    assert [order.client_order_id for order in service.get_orders()] == ["ata-filled-while-down"]
    assert service.get_positions()[0].quantity == Decimal("0.05")
    assert [trade.quantity for trade in service.get_trades()] == [Decimal("0.05")]
    assert adapter.fetch_closed_orders_calls[0]["since_ms"] is not None
    service._on_execution.assert_awaited_once()

    # A second discovery pass must not double-book the recovered fill.
    assert await service.discover_untracked_orders() == 0
    assert len(service.get_trades()) == 1


async def test_real_order_and_close_preserve_decision_attribution() -> None:
    service, _ = make_service()
    decision_id = uuid4()

    order = await service.place_order(
        buy_request(coordinator_decision_id=decision_id),
        current_market_price=50_000,
        confirm=True,
    )
    assert order.coordinator_decision_id == decision_id
    assert service.get_positions()[0].coordinator_decision_id == decision_id
    assert service.get_trades()[0].coordinator_decision_id == decision_id

    position = service.get_positions()[0]
    sell = await service.close_position(
        position_id=position.id,
        current_market_price=51_000,
        confirm=True,
    )

    assert sell is not None
    assert sell.coordinator_decision_id == decision_id
    assert service.get_trades()[-1].coordinator_decision_id == decision_id


async def test_place_order_risk_rejection_does_not_record_position() -> None:
    service, _ = make_service(risk=FakeRisk(allowed=False))

    with pytest.raises(RiskViolationError, match="Risk Guard"):
        await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert service.get_positions() == []


async def test_live_preflight_includes_existing_portfolio_exposure() -> None:
    risk = FakeRisk()
    service, _ = make_service(risk=risk)

    await service.place_order(buy_request(quantity=0.1), current_market_price=50_000, confirm=True)
    await service.place_order(buy_request(quantity=0.1), current_market_price=50_000, confirm=True)

    assert risk.last_request.current_symbol_exposure_usd == pytest.approx(5_000)
    assert risk.last_request.current_total_exposure_usd == pytest.approx(5_000)


async def test_close_position_sells_and_removes_position() -> None:
    service, adapter = make_service()
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    position = service.get_positions()[0]

    order = await service.close_position(
        position_id=position.id,
        current_market_price=51_000,
        confirm=True,
    )

    assert order is not None
    assert order.side == OrderSide.SELL
    assert adapter.last_place_kwargs["side"] == OrderSide.SELL
    assert service.get_positions() == []
    assert service.get_portfolio().total_realized_pnl > 0


async def test_close_unknown_position_returns_none() -> None:
    service, _ = make_service()
    from uuid import uuid4

    assert await service.close_position(uuid4(), current_market_price=50_000) is None


async def test_close_symbol_uses_free_balance() -> None:
    service, adapter = make_service()

    order = await service.close_symbol(
        "BTC/USDT",
        quantity=None,
        current_market_price=50_000,
        confirm=True,
    )

    assert order.side == OrderSide.SELL
    assert adapter.last_place_kwargs["quantity"] == 0.5


async def test_close_symbol_without_balance_raises() -> None:
    service, adapter = make_service()
    adapter.free_balance = 0.0

    with pytest.raises(ValidationError, match="No free BTC balance"):
        await service.close_symbol("BTC/USDT", confirm=True)


async def test_protective_exit_triggers_stop_loss() -> None:
    alerts = FakeAlert()
    service, _ = make_service(alerts=alerts)
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    exits = await service.check_protective_exits({"BTC/USDT": 48_000})

    assert len(exits) == 1
    assert service.get_positions() == []
    assert any(call[2] == AlertSeverity.CRITICAL for call in alerts.calls)


async def test_protective_exit_triggers_take_profit() -> None:
    service, _ = make_service()
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    exits = await service.check_protective_exits({"BTC/USDT": 56_000})

    assert len(exits) == 1


async def test_protective_exit_ignores_prices_inside_range() -> None:
    service, _ = make_service()
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    exits = await service.check_protective_exits({"BTC/USDT": 52_000})

    assert exits == []
    assert len(service.get_positions()) == 1


async def test_account_balances_updates_portfolio_cash() -> None:
    service, _ = make_service()

    balances = await service.account_balances()

    assert balances["USDT"] == 10_000.0
    assert service.get_portfolio().available_balance == Decimal("10000.0")


async def test_guard_starts_and_stops() -> None:
    service, _ = make_service()

    async def price_fetcher(_symbol: str) -> float | None:
        return 50_000.0

    service.start_guard(price_fetcher)
    assert service.status().guard_running is True

    await service.stop_guard()
    assert service.status().guard_running is False


# ---------------------------------------------------------------------------
# Hardening: idempotency, hydration and reconciliation
# ---------------------------------------------------------------------------


async def test_place_order_is_idempotent_with_client_order_id() -> None:
    service, adapter = make_service()

    first = await service.place_order(
        buy_request(client_order_id="client-1"),
        current_market_price=50_000,
        confirm=True,
    )
    second = await service.place_order(
        buy_request(client_order_id="client-1"),
        current_market_price=50_000,
        confirm=True,
    )

    assert second.id == first.id
    assert adapter.place_calls == 1
    assert len(service.get_positions()) == 1
    assert len(service.get_orders()) == 1


async def test_place_order_rejects_overlong_client_order_id() -> None:
    service, _ = make_service()

    with pytest.raises(ValidationError, match="36 characters"):
        await service.place_order(
            buy_request(client_order_id="x" * 37),
            current_market_price=50_000,
            confirm=True,
        )


async def test_restore_rebuilds_ledger_and_idempotency() -> None:
    service, _ = make_service()
    original = await service.place_order(
        buy_request(client_order_id="restored-1"),
        current_market_price=50_000,
        confirm=True,
    )

    restored, adapter = make_service()
    restored.restore(
        positions=service.get_positions(),
        orders=service.get_orders(),
        trades=service.get_trades(),
        available_balance=Decimal("9000"),
        realized_pnl=Decimal("12"),
        peak_value=Decimal("11000"),
    )

    assert len(restored.get_positions()) == 1
    assert len(restored.get_orders()) == 1
    assert len(restored.get_trades()) == 1
    assert restored.get_portfolio().available_balance == Decimal("9000")
    assert restored.get_portfolio().total_realized_pnl == Decimal("12")

    again = await restored.place_order(
        buy_request(client_order_id="restored-1"),
        current_market_price=50_000,
        confirm=True,
    )
    assert again.id == original.id
    assert adapter.place_calls == 0


async def test_hydrate_live_service_restores_journal_and_snapshot() -> None:
    source, _ = make_service()
    order = await source.place_order(
        buy_request(client_order_id="hydrated-1"),
        current_market_price=50_000,
        confirm=True,
    )
    snapshot = Portfolio(
        mode=ExecutionMode.REAL,
        exchange=Exchange.BINANCE,
        total_balance=Decimal("10500"),
        available_balance=Decimal("9000"),
        locked_balance=Decimal("0"),
        total_realized_pnl=Decimal("25"),
        peak_value=Decimal("11000"),
    )
    repo = FakeRepo(
        positions=source.get_positions(),
        orders=source.get_orders(),
        trades=source.get_trades(),
        snapshot=snapshot,
    )

    target, _ = make_service()
    report = await hydrate_live_trading_service(target, repo)  # type: ignore[arg-type]

    assert report.positions_restored == 1
    assert report.orders_restored == 1
    assert report.trades_restored == 1
    assert report.balance_restored is True
    assert report.exchanges == ["binance"]
    assert target.get_positions()[0].id == source.get_positions()[0].id
    assert target.get_orders()[0].id == order.id
    assert target.get_portfolio().available_balance == Decimal("9000")


async def test_reconcile_removes_position_closed_on_exchange() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(alerts=alerts)
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.balances = {"USDT": 10_000.0}

    removed = await service.reconcile_positions()

    assert removed == ["BTC/USDT"]
    assert service.get_positions() == []
    assert any("cerrada fuera" in call[0] for call in alerts.calls)


async def test_reconcile_reduces_quantity_to_exchange_balance() -> None:
    service, adapter = make_service()
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.balances = {"USDT": 10_000.0, "BTC": 0.05}

    removed = await service.reconcile_positions()

    assert removed == []
    assert service.get_positions()[0].quantity == Decimal("0.05")


async def test_reconcile_persists_changes_through_callback() -> None:
    service, adapter = make_service()
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.balances = {"USDT": 10_000.0}
    calls: list[bool] = []

    async def on_reconcile() -> None:
        calls.append(True)

    service._on_reconcile = on_reconcile  # type: ignore[method-assign]

    removed = await service.reconcile_positions()

    assert removed == ["BTC/USDT"]
    assert calls == [True]


async def test_reconcile_is_skipped_when_real_trading_disabled() -> None:
    service, _ = make_service(settings=Settings(_env_file=None))
    service.restore(positions=[make_position()], orders=[], trades=[])

    assert await service.reconcile_positions() == []
    assert len(service.get_positions()) == 1


async def test_notify_restored_positions_warns_when_disabled() -> None:
    alerts = FakeAlert()
    service, _ = make_service(settings=Settings(_env_file=None), alerts=alerts)
    service.restore(positions=[make_position()], orders=[], trades=[])

    await service.notify_restored_positions()

    assert any(
        call[2] == AlertSeverity.CRITICAL and "sin protección" in call[0] for call in alerts.calls
    )


async def test_protective_exit_failure_alerts_and_keeps_position() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(alerts=alerts)
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.fail_on_sell = True

    exits = await service.check_protective_exits({"BTC/USDT": 48_000})

    assert exits == []
    assert len(service.get_positions()) == 1
    assert any("Fallo al ejecutar protección" in call[0] for call in alerts.calls)


# ---------------------------------------------------------------------------
# Exchange-side OCO protection
# ---------------------------------------------------------------------------


async def test_oco_is_created_after_filled_buy() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(
        settings=real_settings(real_trading_oco_enabled=True), alerts=alerts
    )

    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert len(adapter.oco_create_calls) == 1
    call = adapter.oco_create_calls[0]
    assert call["symbol"] == "BTC/USDT"
    assert call["quantity"] == Decimal("0.1")
    assert call["take_profit"] == Decimal("55000")
    assert call["stop_loss"] == Decimal("49000")
    assert call["stop_limit_price"] < Decimal("49000")
    assert service._oco_protections["BTC/USDT"].order_list_id == "777"
    assert service.status().protection_mode == "exchange_oco"
    assert service.status().oco_protected_positions == 1
    assert any("OCO real colocada" in alert[0] for alert in alerts.calls)


async def test_oco_not_created_when_disabled() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=False))

    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert adapter.oco_create_calls == []
    assert service.status().protection_mode == "local_guard"
    assert service.status().oco_enabled is False


async def test_local_guard_skips_oco_protected_position() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.place_calls = 0

    exits = await service.check_protective_exits({"BTC/USDT": 48_000})

    assert exits == []
    assert adapter.place_calls == 0


async def test_oco_creation_failure_falls_back_to_local_guard() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(
        settings=real_settings(real_trading_oco_enabled=True), alerts=alerts
    )
    adapter.fail_oco_create = True

    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert "BTC/USDT" not in service._oco_protections
    assert any("OCO no colocada" in alert[0] for alert in alerts.calls)
    adapter.place_calls = 0

    exits = await service.check_protective_exits({"BTC/USDT": 48_000})

    assert len(exits) == 1
    assert adapter.place_calls == 1


async def test_direct_sell_cancels_oco_before_submitting() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.place_calls = 0

    order = await service.place_order(
        PlaceOrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.1,
        ),
        current_market_price=50_000,
        confirm=True,
    )

    assert order.side == OrderSide.SELL
    assert adapter.oco_cancel_calls == [("BTC/USDT", "777")]
    assert adapter.place_calls == 1
    assert "BTC/USDT" not in service._oco_protections


async def test_sell_blocked_when_oco_discovery_failed() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    service.restore(positions=[make_position()], orders=[], trades=[])
    adapter.fail_open_oco = True
    await service.discover_oco_protections()
    adapter.place_calls = 0

    with pytest.raises(SecurityError, match="no se pudieron descubrir"):
        await service.place_order(
            PlaceOrderRequest(
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=0.1,
            ),
            current_market_price=50_000,
            confirm=True,
        )

    assert adapter.place_calls == 0


async def test_close_position_blocked_when_oco_discovery_failed() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    service.restore(positions=[make_position()], orders=[], trades=[])
    adapter.fail_open_oco = True
    await service.discover_oco_protections()
    position = service.get_positions()[0]
    adapter.place_calls = 0

    with pytest.raises(SecurityError, match="no se pudieron descubrir"):
        await service.close_position(position.id, 50_000, confirm=True)

    assert adapter.place_calls == 0


async def test_close_position_cancels_oco_before_selling() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    position = service.get_positions()[0]
    adapter.place_calls = 0

    order = await service.close_position(position.id, 50_000, confirm=True)

    assert order is not None
    assert adapter.oco_cancel_calls == [("BTC/USDT", "777")]
    assert adapter.place_calls == 1
    assert "BTC/USDT" not in service._oco_protections


async def test_close_position_aborts_when_oco_cancel_fails() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    position = service.get_positions()[0]
    adapter.fail_oco_cancel = True
    adapter.oco_status = {
        "orderListId": 777,
        "listOrderStatus": "EXECUTING",
        "orders": [],
    }
    adapter.place_calls = 0

    with pytest.raises(ExchangeError, match="doble venta"):
        await service.close_position(position.id, 50_000, confirm=True)

    assert adapter.place_calls == 0
    assert "BTC/USDT" in service._oco_protections


async def test_close_position_handles_oco_already_executed() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(
        settings=real_settings(real_trading_oco_enabled=True), alerts=alerts
    )
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    position = service.get_positions()[0]
    adapter.fail_oco_cancel = True
    adapter.oco_status = {
        "orderListId": 777,
        "listOrderStatus": "ALL_DONE",
        "orders": [
            {
                "status": "FILLED",
                "executedQty": "0.1",
                "cummulativeQuoteQty": "5500",
                "price": "55000",
                "type": "LIMIT",
                "side": "SELL",
            }
        ],
    }
    adapter.place_calls = 0

    order = await service.close_position(position.id, 50_000, confirm=True)

    assert order is not None
    assert order.average_fill_price == Decimal("55000")
    assert adapter.place_calls == 0
    assert service.get_positions() == []
    assert service.get_portfolio().total_realized_pnl == Decimal("500")
    assert any("OCO real ejecutado" in alert[0] for alert in alerts.calls)


async def test_sync_oco_executed_updates_ledger() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(
        settings=real_settings(real_trading_oco_enabled=True), alerts=alerts
    )
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.oco_status = {
        "orderListId": 777,
        "listOrderStatus": "ALL_DONE",
        "orders": [
            {
                "status": "FILLED",
                "executedQty": "0.1",
                "cummulativeQuoteQty": "5500",
                "price": "55000",
                "type": "LIMIT",
                "side": "SELL",
            }
        ],
    }
    adapter.place_calls = 0

    exits = await service.sync_oco_protections()

    assert len(exits) == 1
    assert service.get_positions() == []
    assert "BTC/USDT" not in service._oco_protections
    assert len(service.get_trades()) == 2
    assert adapter.place_calls == 0
    assert any("OCO real ejecutado" in alert[0] for alert in alerts.calls)


async def test_sync_oco_cancelled_resumes_local_guard() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    adapter.oco_status = {
        "orderListId": 777,
        "listOrderStatus": "ALL_CANCELED",
        "orders": [],
    }

    await service.sync_oco_protections()

    assert "BTC/USDT" not in service._oco_protections
    adapter.place_calls = 0

    exits = await service.check_protective_exits({"BTC/USDT": 48_000})

    assert len(exits) == 1


async def test_discover_oco_attaches_open_lists() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))
    service.restore(positions=[make_position()], orders=[], trades=[])
    adapter.open_oco_lists = [
        {
            "orderListId": 999,
            "symbol": "BTCUSDT",
            "listOrderStatus": "EXECUTING",
            "listClientOrderId": "oco-existing",
        }
    ]

    discovered = await service.discover_oco_protections()

    assert discovered == 1
    protection = service._oco_protections["BTC/USDT"]
    assert protection.order_list_id == "999"
    assert protection.list_client_order_id == "oco-existing"
    assert protection.quantity == Decimal("0.1")
    assert service.status().oco_protected_positions == 1


async def test_discover_failure_blocks_local_guard() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(
        settings=real_settings(real_trading_oco_enabled=True), alerts=alerts
    )
    service.restore(positions=[make_position()], orders=[], trades=[])
    adapter.fail_open_oco = True

    discovered = await service.discover_oco_protections()

    assert discovered == 0
    assert Exchange.BINANCE in service._oco_discovery_failed
    assert service.status().protection_mode == "oco_discovery_failed"
    assert service.status().oco_discovery_failed_exchanges == ["binance"]
    adapter.place_calls = 0

    exits = await service.check_protective_exits({"BTC/USDT": 48_000})

    assert exits == []
    assert adapter.place_calls == 0
    assert any("No se pudieron descubrir OCO" in alert[0] for alert in alerts.calls)


async def test_multiple_open_ocos_block_exchange() -> None:
    alerts = FakeAlert()
    service, adapter = make_service(
        settings=real_settings(real_trading_oco_enabled=True), alerts=alerts
    )
    service.restore(positions=[make_position()], orders=[], trades=[])
    adapter.open_oco_lists = [
        {
            "orderListId": 1,
            "symbol": "BTCUSDT",
            "listOrderStatus": "EXECUTING",
        },
        {
            "orderListId": 2,
            "symbol": "BTCUSDT",
            "listOrderStatus": "EXECUTING",
        },
    ]

    discovered = await service.discover_oco_protections()

    assert discovered == 0
    assert Exchange.BINANCE in service._oco_discovery_failed
    assert "BTC/USDT" not in service._oco_protections
    assert any("No se pudieron descubrir OCO" in alert[0] for alert in alerts.calls)


async def test_adding_to_position_replaces_oco_with_full_quantity() -> None:
    service, adapter = make_service(settings=real_settings(real_trading_oco_enabled=True))

    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)
    await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert len(adapter.oco_create_calls) == 2
    assert adapter.oco_create_calls[1]["quantity"] == Decimal("0.2")
    assert adapter.oco_cancel_calls == [("BTC/USDT", "777")]
    assert service._oco_protections["BTC/USDT"].quantity == Decimal("0.2")
    assert service.get_positions()[0].quantity == Decimal("0.2")


# ---------------------------------------------------------------------------
# AlertService
# ---------------------------------------------------------------------------


class FakeChannel(NotificationPort):
    def __init__(
        self,
        name: str = "fake",
        configured: bool = True,
        result: bool | Exception = True,
    ) -> None:
        self._name = name
        self._configured = configured
        self._result = result
        self.sent: list[Alert] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_alert_service_disabled_does_not_send() -> None:
    channel = FakeChannel()
    service = AlertService([channel], enabled=False)

    assert await service.notify("t", "m") is False
    assert channel.sent == []
    assert service.status().enabled is False


async def test_alert_service_filters_by_severity() -> None:
    channel = FakeChannel()
    service = AlertService([channel], enabled=True, min_severity=AlertSeverity.WARNING)

    delivered = await service.notify("t", "m", AlertSeverity.INFO)

    assert delivered is False
    assert channel.sent == []


async def test_alert_service_delivers_and_counts() -> None:
    channel = FakeChannel()
    service = AlertService([channel], enabled=True)

    assert await service.notify("Orden", "Ejecutada", AlertSeverity.SUCCESS) is True

    status = service.status()
    assert status.sent_count == 1
    assert status.last_sent_at is not None
    assert status.last_error is None


async def test_alert_service_survives_channel_exceptions() -> None:
    channel = FakeChannel(result=RuntimeError("telegram down"))
    service = AlertService([channel], enabled=True)

    assert await service.notify("t", "m") is False

    status = service.status()
    assert status.failed_count == 1
    assert "telegram down" in (status.last_error or "")


async def test_alert_service_requires_configured_channel() -> None:
    channel = FakeChannel(configured=False)
    service = AlertService([channel], enabled=True)

    assert await service.notify("t", "m") is False
    assert channel.sent == []


async def test_alert_service_from_settings_builds_channels() -> None:
    settings = Settings(
        _env_file=None,
        alerts_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        alert_webhook_url="https://example.test/hook",
        email_smtp_host="smtp.example.test",
        email_from="bot@example.test",
        email_to="ops@example.test",
        alert_min_severity="warning",
    )
    service = AlertService.from_settings(settings)

    names = {channel.name for channel in service.status().channels}
    assert names == {"telegram", "webhook", "email"}
    status = service.status()
    assert status.enabled is True
    assert status.min_severity == AlertSeverity.WARNING


async def test_alert_service_invalid_min_severity_falls_back_to_info() -> None:
    service = AlertService.from_settings(
        Settings(_env_file=None, alerts_enabled=True, alert_min_severity="nonsense")
    )

    assert service.status().min_severity == AlertSeverity.INFO


async def test_autonomous_engine_blocks_real_mode_by_default(monkeypatch: Any) -> None:
    from src.application.services import autonomous_engine as engine_module
    from src.application.services.autonomous_engine import AutonomousEngine
    from src.shared.exceptions.domain import ValidationError as DomainValidationError

    monkeypatch.setattr(engine_module, "get_settings", lambda: Settings(_env_file=None))
    engine = AutonomousEngine()
    engine.config.execution_mode = "real"

    with pytest.raises(DomainValidationError, match="bot autónomo en modo real está deshabilitado"):
        await engine.start()


async def test_autonomous_engine_stop_is_safe_when_idle() -> None:
    from src.application.services.autonomous_engine import AutonomousEngine

    engine = AutonomousEngine()
    engine.is_running = True
    engine._task = None

    status = await engine.stop()
    assert status.is_running is False
