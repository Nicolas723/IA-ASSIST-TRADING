"""Watchdog, end-of-day summary and Telegram commands for the assistant.

Telegram only reports trades, so silence is ambiguous: no trades, or a dead
loop? This monitor runs next to the engine and speaks up when the loop stalls,
keeps failing, or was left stopped by a backend restart. It also sends one
summary per day and answers the chat commands (/estado, /pausar, ...).

A dead *process* cannot alert by itself; that case is covered by the external
heartbeat (``HEARTBEAT_PING_URL``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src.application.services.trade_alerts import money
from src.domain.analytics.entities import DecisionOutcomeStatus
from src.domain.notifications.entities import Alert, AlertSeverity
from src.domain.trading.entities import ExecutionMode
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.alert_service import AlertService
    from src.application.services.autonomous_engine import AutonomousEngine
    from src.application.services.live_trading_service import LiveTradingService
    from src.domain.analytics.entities import DecisionOutcome
    from src.domain.trading.entities import Order

logger = get_logger(__name__)

_DEFAULT_STATE_FILE = Path(__file__).resolve().parents[3] / "vault" / ".assistant_monitor.json"
ERRORS_BEFORE_ALERT = 3
MIN_STALL_SECONDS = 900

OutcomesLoader = Callable[[datetime], Awaitable[list["DecisionOutcome"]]]
OrderJournal = Callable[["Order"], Awaitable[Any]]

_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

HELP_TEXT = (
    "🤖 <b>Comandos</b>\n"
    "/estado · saldo, posiciones y si el asistente está en marcha\n"
    "/resumen · resultado de hoy\n"
    "/pausar · detiene el asistente (las posiciones siguen protegidas)\n"
    "/emergencia · cierra todas las posiciones a mercado"
)


def _pct(value: float) -> str:
    return f"{value:+.2f} %".replace(".", ",")


def _signed_money(value: float) -> str:
    return f"+{money(value)}" if value >= 0 else f"-{money(-value)}"


def _default_state_path() -> Path | None:
    configured = os.getenv("ASSISTANT_MONITOR_STATE_PATH", "").strip()
    if configured:
        return Path(configured)
    if "pytest" in sys.modules:
        return None
    return _DEFAULT_STATE_FILE


def resolve_timezone(name: str) -> tzinfo:
    """Configured IANA zone, else the server's local zone."""
    if name.strip():
        try:
            return ZoneInfo(name.strip())
        except Exception:
            logger.warning("alert_timezone_invalid", timezone=name)
    return datetime.now().astimezone().tzinfo or UTC


class AssistantMonitor:
    """Background checks plus the text behind each Telegram command."""

    def __init__(
        self,
        *,
        alert_service: AlertService,
        engine: Callable[[], AutonomousEngine | None],
        live: Callable[[], LiveTradingService | None],
        outcomes: OutcomesLoader | None = None,
        journal: OrderJournal | None = None,
        llm_usage: Callable[[], dict[str, object] | None] | None = None,
        testnet: Callable[[], bool] = lambda: True,
        summary_hour: int = 22,
        timezone: tzinfo = UTC,
        interval_seconds: int = 60,
        state_path: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._alerts = alert_service
        self._engine = engine
        self._live = live
        self._outcomes = outcomes
        self._journal = journal
        self._llm_usage = llm_usage
        self._testnet = testnet
        self._summary_hour = summary_hour
        self._tz = timezone
        self._interval = max(10, interval_seconds)
        self._state_path = state_path if state_path is not None else _default_state_path()
        self._clock = clock
        self._state = self._load_state()
        self._stalled = False
        self._failing = False
        self._checked_restart = False
        self._emergency_armed_until: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="assistant-monitor")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # pragma: no cover - never kill the monitor
                logger.warning("assistant_monitor_tick_failed", error=str(exc))
            await asyncio.sleep(self._interval)

    async def tick(self) -> None:
        """One pass: restart check, watchdog, then the daily summary."""
        await self._check_restart()
        await self._check_engine()
        await self._maybe_send_summary()

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    async def _check_restart(self) -> None:
        """Warn once if the backend came back up with the assistant stopped."""
        if self._checked_restart:
            return
        self._checked_restart = True
        engine = self._engine()
        if self._state.get("engine_running") and not (engine and engine.is_running):
            await self._send(
                "Asistente detenido por un reinicio",
                "El backend se reinició mientras el asistente estaba en marcha y quedó en "
                "pausa. Ábrelo y pulsa Iniciar para reanudarlo.",
                AlertSeverity.WARNING,
            )

    async def _check_engine(self) -> None:
        engine = self._engine()
        running = bool(engine and engine.is_running)
        if self._state.get("engine_running") != running:
            self._state["engine_running"] = running
            self._save_state()
        if engine is None or not running:
            self._stalled = self._failing = False
            return

        now = self._clock()
        threshold = max(3 * engine.config.interval_seconds, MIN_STALL_SECONDS)
        last = engine.last_cycle_at or engine.started_at
        task = getattr(engine, "_task", None)
        stalled = (task is not None and task.done()) or (
            last is not None and (now - last).total_seconds() > threshold
        )
        if stalled and not self._stalled:
            minutes = int((now - last).total_seconds() // 60) if last else 0
            await self._send(
                "El asistente no responde",
                f"Lleva {minutes} min sin completar un ciclo. Las posiciones abiertas siguen "
                "protegidas por la OCO o la vigilancia local. Revisa el backend.",
                AlertSeverity.CRITICAL,
            )
        elif not stalled and self._stalled:
            await self._send(
                "El asistente volvió a funcionar",
                "Los ciclos se completan de nuevo con normalidad.",
                AlertSeverity.SUCCESS,
            )
        self._stalled = stalled

        failing = engine.consecutive_errors >= ERRORS_BEFORE_ALERT
        if failing and not self._failing:
            await self._send(
                "El asistente está fallando",
                f"{engine.consecutive_errors} ciclos seguidos con error. Último: "
                f"{(engine.last_error or 'desconocido')[:200]}",
                AlertSeverity.CRITICAL,
            )
        elif not failing and self._failing:
            await self._send(
                "El asistente se recuperó",
                "Los ciclos vuelven a completarse sin errores.",
                AlertSeverity.SUCCESS,
            )
        self._failing = failing

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def _local_now(self) -> datetime:
        return self._clock().astimezone(self._tz)

    async def _maybe_send_summary(self) -> None:
        if self._summary_hour < 0:
            return
        now = self._local_now()
        today = now.date().isoformat()
        if now.hour < self._summary_hour or self._state.get("summary_sent") == today:
            return
        self._state["summary_sent"] = today
        self._save_state()
        html, plain, activity = await self.summary(now.date())
        if not activity:
            logger.info("daily_summary_skipped_no_activity", day=today)
            return
        await self._alerts.send(
            Alert(
                title="Resumen del día",
                message=plain,
                severity=AlertSeverity.INFO,
                metadata={"event": "daily_summary", "html": html, "day": today},
            )
        )

    async def summary(self, day: date) -> tuple[str, str, bool]:
        """(telegram html, plain text, had activity) for one local day."""
        start = datetime(day.year, day.month, day.day, tzinfo=self._tz)
        end = start + timedelta(days=1)
        closed: list[DecisionOutcome] = []
        if self._outcomes is not None:
            try:
                outcomes = await self._outcomes(start - timedelta(days=7))
            except Exception as exc:
                logger.warning("daily_summary_outcomes_failed", error=str(exc))
                outcomes = []
            closed = [
                outcome
                for outcome in outcomes
                if outcome.status == DecisionOutcomeStatus.CLOSED
                and outcome.closed_at is not None
                and start <= _aware(outcome.closed_at) < end
            ]
        pnl = sum(float(outcome.net_pnl) for outcome in closed)
        wins = sum(1 for outcome in closed if outcome.net_pnl > 0)
        losses = len(closed) - wins
        live = self._live()
        open_count = len(live.get_positions(ExecutionMode.REAL)) if live else 0
        engine = self._engine()
        running = bool(engine and engine.is_running)

        head = f"📊 <b>RESUMEN · {day.day} {_MONTHS[day.month - 1]}</b>"
        lines = [head]
        if closed:
            rate = round(100 * wins / len(closed))
            word = "operación" if len(closed) == 1 else "operaciones"
            lines += [
                f"<b>{_signed_money(pnl)}</b>  ·  {len(closed)} {word}",
                "",
                f"✅ {wins}   🔻 {losses}   ·  {rate} % aciertos",
            ]
        else:
            lines += ["Sin operaciones cerradas hoy", ""]
        details = []
        if open_count:
            details.append(f"📂 {open_count} abierta{'s' if open_count != 1 else ''}")
        cost = self._llm_cost()
        if cost is not None:
            details.append(f"🧠 IA {money(cost)}")
        details.append("🧪 Testnet" if self._testnet() else "💰 Mainnet")
        lines.append("  ·  ".join(details))
        if not running:
            lines.append("⏸ Asistente en pausa")
        plain = (
            f"{len(closed)} operaciones cerradas, resultado {_signed_money(pnl)}, "
            f"{wins} ganadoras y {losses} perdedoras."
        )
        return "\n".join(lines), plain, bool(closed or open_count or running)

    def _llm_cost(self) -> float | None:
        if self._llm_usage is None:
            return None
        try:
            usage = self._llm_usage()
        except Exception:
            return None
        if not usage:
            return None
        pricing = usage.get("pricing")
        if not isinstance(pricing, dict) or not pricing.get("configured"):
            return None
        cost = usage.get("estimated_cost_usd")
        return float(cost) if isinstance(cost, int | float) else None

    # ------------------------------------------------------------------
    # Telegram commands
    # ------------------------------------------------------------------

    async def handle_command(self, text: str) -> str:
        """Reply (Telegram HTML) to one chat command."""
        parts = text.strip().split()
        command = parts[0].split("@", 1)[0].lower() if parts else ""
        argument = parts[1].lower() if len(parts) > 1 else ""
        if command in {"/estado", "/status"}:
            return self.status()
        if command in {"/resumen", "/summary"}:
            html, _, _ = await self.summary(self._local_now().date())
            return html
        if command in {"/pausar", "/pause", "/stop"}:
            return await self._pause()
        if command in {"/emergencia", "/emergency"}:
            return await self._emergency(argument)
        return HELP_TEXT

    def status(self) -> str:
        engine = self._engine()
        running = bool(engine and engine.is_running)
        lines = ["🟢 <b>ASISTENTE EN MARCHA</b>" if running else "⏸ <b>ASISTENTE EN PAUSA</b>"]
        meta = ["🧪 Testnet" if self._testnet() else "💰 Mainnet"]
        if running and engine is not None and engine.last_cycle_at is not None:
            minutes = int((self._clock() - engine.last_cycle_at).total_seconds() // 60)
            meta.append("último ciclo ahora" if minutes < 1 else f"último ciclo hace {minutes} min")
        lines.append("  ·  ".join(meta))

        live = self._live()
        if live is None:
            return "\n".join(lines)
        try:
            portfolio = live.get_portfolio(ExecutionMode.REAL)
        except Exception as exc:
            logger.warning("telegram_status_portfolio_failed", error=str(exc))
            return "\n".join(lines)
        total = float(portfolio.total_balance)
        daily = float(portfolio.daily_pnl)
        start = total - daily
        daily_pct = daily / start * 100 if start > 0 else 0.0
        lines += ["", f"💵 <b>{money(total)}</b>  ·  hoy {_pct(daily_pct)}"]
        if not portfolio.positions:
            lines.append("Sin posiciones abiertas")
        for position in portfolio.positions:
            lines.append(
                f"📂 {position.symbol}  {money(position.entry_price)} → "
                f"{money(position.current_price)}  ({_pct(float(position.unrealized_pnl_pct))})"
            )
        return "\n".join(lines)

    async def _pause(self) -> str:
        engine = self._engine()
        if engine is None or not engine.is_running:
            return "⏸ El asistente ya estaba en pausa."
        await engine.stop()
        self._state["engine_running"] = False
        self._save_state()
        return (
            "⏸ <b>Asistente en pausa</b>\n"
            "No abrirá operaciones nuevas. Las posiciones abiertas siguen protegidas.\n"
            "Para reanudarlo, pulsa Iniciar en la app."
        )

    async def _emergency(self, argument: str) -> str:
        now = self._clock()
        armed = self._emergency_armed_until is not None and now <= self._emergency_armed_until
        if argument != "confirmar" or not armed:
            self._emergency_armed_until = now + timedelta(minutes=2)
            return (
                "🚨 <b>¿Cerrar todo?</b>\n"
                "Detiene el asistente y vende todas las posiciones a mercado.\n"
                "Envía <code>/emergencia confirmar</code> en los próximos 2 minutos."
            )
        self._emergency_armed_until = None
        engine = self._engine()
        if engine is not None and engine.is_running:
            await engine.stop()
        self._state["engine_running"] = False
        self._save_state()
        live = self._live()
        if live is None:
            return "⏸ Asistente detenido. No hay servicio de trading real activo."
        closed, failed = 0, 0
        for position in list(live.get_positions(ExecutionMode.REAL)):
            try:
                order = await live.close_position(
                    position_id=position.id,
                    current_market_price=float(position.current_price or position.entry_price),
                    mode=ExecutionMode.REAL,
                    confirm=True,
                    exchange=position.exchange,
                    reason="emergency",
                )
            except Exception as exc:
                failed += 1
                logger.error(
                    "telegram_emergency_close_failed", symbol=position.symbol, error=str(exc)
                )
                continue
            if order is None:
                failed += 1
                continue
            closed += 1
            if self._journal is not None:
                try:
                    await self._journal(order)
                except Exception as exc:
                    logger.warning("telegram_emergency_journal_failed", error=str(exc))
        text = f"🚨 <b>Parada de emergencia</b>\nAsistente detenido · {closed} posiciones cerradas"
        if failed:
            text += f"\n⚠️ {failed} no se pudieron cerrar: revísalas en Binance."
        return text

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send(self, title: str, message: str, severity: AlertSeverity) -> None:
        try:
            await self._alerts.send(
                Alert(
                    title=title, message=message, severity=severity, metadata={"event": "watchdog"}
                )
            )
        except Exception as exc:  # pragma: no cover - alerting is best-effort
            logger.warning("assistant_monitor_alert_failed", error=str(exc))

    def _load_state(self) -> dict[str, Any]:
        if self._state_path is None:
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._state), encoding="utf-8")
        except OSError as exc:
            logger.warning("assistant_monitor_state_save_failed", error=str(exc))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
