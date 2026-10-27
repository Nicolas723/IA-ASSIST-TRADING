"""Watchdog, daily summary, Telegram commands and the daily loss limit."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from src.application.services.assistant_monitor import AssistantMonitor
from src.domain.analytics.entities import DecisionOutcome, DecisionOutcomeStatus
from src.domain.risk.drawdown_guard import DrawdownGuard
from src.domain.risk.entities import OrderRiskCheckRequest, RiskProfile
from src.domain.trading.entities import (
    Exchange,
    ExecutionMode,
    Portfolio,
    Position,
    PositionSide,
)
from src.infrastructure.notifications.telegram import render
from src.infrastructure.notifications.telegram_commands import TelegramCommandListener

if TYPE_CHECKING:
    from pathlib import Path

    from src.domain.notifications.entities import Alert

NOW = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)


class _Alerts:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    async def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True

    @property
    def titles(self) -> list[str]:
        return [alert.title for alert in self.sent]


@dataclass
class _Config:
    interval_seconds: int = 60


@dataclass
class _Engine:
    is_running: bool = True
    config: _Config = field(default_factory=_Config)
    last_cycle_at: datetime | None = NOW
    started_at: datetime | None = NOW - timedelta(hours=1)
    consecutive_errors: int = 0
    last_error: str | None = None
    stops: int = 0

    async def stop(self) -> None:
        self.is_running = False
        self.stops += 1


def _position(symbol: str = "BTC/USDT") -> Position:
    return Position(
        symbol=symbol,
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.REAL,
        side=PositionSide.LONG,
        entry_price=Decimal("100"),
        quantity=Decimal("1"),
        current_price=Decimal("102"),
    )


class _Live:
    def __init__(self, positions: list[Position] | None = None) -> None:
        self.positions = positions or []
        self.closed: list[str] = []

    def get_positions(self, _mode: ExecutionMode) -> list[Position]:
        return list(self.positions)

    def get_portfolio(self, mode: ExecutionMode) -> Portfolio:
        return Portfolio(
            mode=mode,
            exchange=Exchange.BINANCE,
            total_balance=Decimal("1000"),
            available_balance=Decimal("898"),
            locked_balance=Decimal("0"),
            positions=self.positions,
            daily_pnl=Decimal("-10"),
        )

    async def close_position(self, *, position_id: Any, reason: str, **_: Any) -> object:
        position = next(p for p in self.positions if p.id == position_id)
        self.positions.remove(position)
        self.closed.append(f"{position.symbol}:{reason}")
        return object()


def _monitor(
    engine: _Engine | None = None,
    live: _Live | None = None,
    *,
    clock: list[datetime] | None = None,
    outcomes: list[DecisionOutcome] | None = None,
    state_path: Path | None = None,
) -> tuple[AssistantMonitor, _Alerts]:
    alerts = _Alerts()
    now = clock or [NOW]

    async def _outcomes(_since: datetime) -> list[DecisionOutcome]:
        return outcomes or []

    monitor = AssistantMonitor(
        alert_service=alerts,  # type: ignore[arg-type]
        engine=lambda: engine,  # type: ignore[arg-type, return-value]
        live=lambda: live,  # type: ignore[arg-type, return-value]
        outcomes=_outcomes,
        summary_hour=22,
        timezone=UTC,
        state_path=state_path,
        clock=lambda: now[0],
    )
    return monitor, alerts


def _closed(pnl: str) -> DecisionOutcome:
    return DecisionOutcome(
        decision_id=uuid4(),
        decision_created_at=NOW - timedelta(hours=3),
        symbol="BTC/USDT",
        signal="buy",
        confidence=0.7,
        mode=ExecutionMode.REAL,
        status=DecisionOutcomeStatus.CLOSED,
        net_pnl=Decimal(pnl),
        closed_at=NOW - timedelta(hours=1),
    )


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


async def test_stalled_loop_alerts_once_then_reports_recovery() -> None:
    engine = _Engine(last_cycle_at=NOW - timedelta(minutes=30))
    clock = [NOW]
    monitor, alerts = _monitor(engine, clock=clock)
    monitor._summary_hour = -1

    await monitor.tick()
    await monitor.tick()
    assert alerts.titles == ["El asistente no responde"]

    engine.last_cycle_at = NOW
    await monitor.tick()
    assert alerts.titles[-1] == "El asistente volvió a funcionar"


async def test_repeated_cycle_errors_alert() -> None:
    engine = _Engine(consecutive_errors=3, last_error="timeout")
    monitor, alerts = _monitor(engine)
    monitor._summary_hour = -1
    await monitor.tick()
    assert alerts.titles == ["El asistente está fallando"]
    assert "timeout" in alerts.sent[0].message


async def test_restart_with_engine_running_warns(tmp_path: Path) -> None:
    state = tmp_path / "monitor.json"
    state.write_text(json.dumps({"engine_running": True}), encoding="utf-8")
    monitor, alerts = _monitor(_Engine(is_running=False), state_path=state)
    monitor._summary_hour = -1
    await monitor.tick()
    await monitor.tick()
    assert alerts.titles == ["Asistente detenido por un reinicio"]
    assert json.loads(state.read_text(encoding="utf-8"))["engine_running"] is False


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------


async def test_daily_summary_is_sent_once_after_the_hour(tmp_path: Path) -> None:
    clock = [NOW.replace(hour=21)]
    monitor, alerts = _monitor(
        _Engine(),
        _Live(),
        clock=clock,
        outcomes=[_closed("6"), _closed("-2")],
        state_path=tmp_path / "s.json",
    )
    await monitor.tick()
    assert alerts.sent == []

    clock[0] = NOW
    await monitor.tick()
    await monitor.tick()
    summaries = [a for a in alerts.sent if a.metadata.get("event") == "daily_summary"]
    assert len(summaries) == 1
    text = render(summaries[0])
    assert text.splitlines()[0] == "📊 <b>RESUMEN · 27 oct</b>"
    assert "<b>+$4,00</b>  ·  2 operaciones" in text
    assert "✅ 1   🔻 1   ·  50 % aciertos" in text


async def test_quiet_day_sends_no_summary() -> None:
    monitor, alerts = _monitor(_Engine(is_running=False), _Live())
    await monitor.tick()
    assert alerts.sent == []


# ---------------------------------------------------------------------------
# Telegram commands
# ---------------------------------------------------------------------------


async def test_status_command_lists_balance_and_positions() -> None:
    monitor, _ = _monitor(_Engine(), _Live([_position()]))
    text = await monitor.handle_command("/estado")
    assert text.startswith("🟢 <b>ASISTENTE EN MARCHA</b>")
    assert "💵 <b>$1.000,00</b>  ·  hoy -0,99 %" in text
    assert "📂 BTC/USDT  $100,00 → $102,00  (+2,00 %)" in text


async def test_pause_command_stops_the_engine() -> None:
    engine = _Engine()
    monitor, _ = _monitor(engine, _Live())
    assert "Asistente en pausa" in await monitor.handle_command("/pausar@MiBot")
    assert engine.stops == 1
    assert "ya estaba en pausa" in await monitor.handle_command("/pausar")


async def test_emergency_needs_confirmation_within_two_minutes() -> None:
    engine = _Engine()
    live = _Live([_position(), _position("ETH/USDT")])
    clock = [NOW]
    monitor, _ = _monitor(engine, live, clock=clock)

    assert "¿Cerrar todo?" in await monitor.handle_command("/emergencia confirmar")
    assert live.closed == []

    clock[0] = NOW + timedelta(minutes=1)
    reply = await monitor.handle_command("/emergencia confirmar")
    assert "2 posiciones cerradas" in reply
    assert live.closed == ["BTC/USDT:emergency", "ETH/USDT:emergency"]
    assert engine.is_running is False


async def test_unknown_command_shows_help() -> None:
    monitor, _ = _monitor()
    assert "/estado" in await monitor.handle_command("/start")


async def test_listener_skips_backlog_and_ignores_other_chats() -> None:
    sent: list[dict[str, Any]] = []
    polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/setMyCommands"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        if request.url.params.get("offset") == "-1":
            # A stale /pausar left from before the restart.
            return httpx.Response(
                200, json={"result": [{"update_id": 9, "message": {"text": "/pausar"}}]}
            )
        polls["count"] += 1
        assert request.url.params.get("offset") == "10"
        return httpx.Response(
            200,
            json={
                "result": [
                    {"update_id": 10, "message": {"text": "/estado", "chat": {"id": 555}}},
                    {"update_id": 11, "message": {"text": "/estado", "chat": {"id": 42}}},
                ]
            },
        )

    async def reply(text: str) -> str:
        return f"ok {text}"

    listener = TelegramCommandListener(credentials=lambda: ("t", "42", True), handler=reply)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        answered = await listener.poll_once(client, "t", "42")
    assert answered == 1
    assert sent == [
        {
            "chat_id": "42",
            "text": "ok /estado",
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ]
    assert polls["count"] == 1


# ---------------------------------------------------------------------------
# Daily loss limit
# ---------------------------------------------------------------------------


def _risk_request(side: str) -> OrderRiskCheckRequest:
    return OrderRiskCheckRequest(
        symbol="BTC/USDT",
        side=side,
        order_type="market",
        price=100.0,
        quantity=0.1,
        stop_loss=98.0,
        take_profit=106.0,
        account_balance=10_000.0,
        current_open_positions_count=1,
        current_daily_loss_pct=2.5,
    )


def test_daily_loss_limit_blocks_buys_but_not_sells() -> None:
    guard = DrawdownGuard(RiskProfile(max_daily_loss_pct=2.0))
    buy = guard.validate_order(_risk_request("BUY"))
    sell = guard.validate_order(_risk_request("SELL"))
    assert not buy.is_allowed
    assert any("Daily loss limit" in reason for reason in buy.violation_reasons)
    assert not any("Daily loss limit" in reason for reason in sell.violation_reasons)


class _NotifyingAlerts(_Alerts):
    async def notify(self, title: str, message: str, *_args: Any, **_kw: Any) -> None:
        from src.domain.notifications.entities import Alert as _Alert

        self.sent.append(_Alert(title=title, message=message))


class _PortfolioService:
    def get_portfolio(self, _mode: ExecutionMode) -> None:
        return None


async def test_engine_daily_limit_alerts_once_per_day() -> None:
    from src.application.services.autonomous_engine import AutonomousEngine
    from src.application.services.risk_service import RiskService

    risk = RiskService(DrawdownGuard(RiskProfile(max_daily_loss_pct=2.0)))
    alerts = _NotifyingAlerts()
    engine = AutonomousEngine(risk_service=risk, alert_service=alerts)  # type: ignore[arg-type]
    service: Any = _PortfolioService()

    risk.current_daily_loss_pct = 1.0
    assert await engine._daily_loss_limit_reached(service, ExecutionMode.REAL) is False

    risk.current_daily_loss_pct = 2.4
    assert await engine._daily_loss_limit_reached(service, ExecutionMode.REAL) is True
    assert await engine._daily_loss_limit_reached(service, ExecutionMode.REAL) is True
    assert alerts.titles == ["Límite diario alcanzado"]
