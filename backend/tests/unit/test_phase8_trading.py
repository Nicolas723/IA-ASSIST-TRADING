"""Unit tests for real-trading execution, safety interlocks and alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.application.services.alert_service import AlertService
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

    async def fetch_balances(self) -> dict[str, float]:
        return self.balances

    async def fetch_free_balance(self, asset: str) -> float:
        return self.free_balance

    async def place_order(self, **kwargs: Any) -> Order:
        self.last_place_kwargs = kwargs
        side = kwargs["side"]
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
) -> PlaceOrderRequest:
    return PlaceOrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


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
