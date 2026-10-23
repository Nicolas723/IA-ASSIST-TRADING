"""Unit tests for real-trading execution, safety interlocks and alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

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

    def validate_order(self, request: Any) -> Any:
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
        self.fail_on_sell = False

    async def fetch_balances(self) -> dict[str, float]:
        return self.balances

    async def fetch_free_balance(self, asset: str) -> float:
        return self.free_balance

    async def place_order(self, **kwargs: Any) -> Order:
        self.last_place_kwargs = kwargs
        self.place_calls += 1
        side = kwargs["side"]
        if self.fail_on_sell and side == OrderSide.SELL:
            raise RuntimeError("sell failed")
        quantity = Decimal(str(kwargs["quantity"]))
        price = Decimal("50_000") if side == OrderSide.BUY else Decimal("51_000")
        return Order(
            symbol=kwargs["symbol"],
            exchange=Exchange.BINANCE,
            mode=ExecutionMode.REAL,
            side=side,
            order_type=kwargs["order_type"],
            quantity=quantity,
            status=OrderStatus.FILLED,
            filled_quantity=quantity,
            average_fill_price=price,
            commission=Decimal("1"),
            client_order_id=kwargs.get("client_order_id"),
            submitted_at=datetime.now(tz=UTC),
            filled_at=datetime.now(tz=UTC),
        )

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
) -> PlaceOrderRequest:
    return PlaceOrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        client_order_id=client_order_id,
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


async def test_place_order_risk_rejection_does_not_record_position() -> None:
    service, _ = make_service(risk=FakeRisk(allowed=False))

    with pytest.raises(RiskViolationError, match="Risk Guard"):
        await service.place_order(buy_request(), current_market_price=50_000, confirm=True)

    assert service.get_positions() == []


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

    with pytest.raises(DomainValidationError, match="Autonomous real trading is disabled"):
        await engine.start()


async def test_autonomous_engine_stop_is_safe_when_idle() -> None:
    from src.application.services.autonomous_engine import AutonomousEngine

    engine = AutonomousEngine()
    engine.is_running = True
    engine._task = None

    status = await engine.stop()
    assert status.is_running is False
