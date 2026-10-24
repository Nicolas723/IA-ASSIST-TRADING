"""
AutonomousEngine — Application Layer for Background Autonomous Trading Loop & Multi-Agent Execution.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from src.domain.market.entities import Timeframe
from src.domain.notifications.entities import AlertSeverity
from src.domain.risk.entities import PositionSizeRequest, RiskProfile
from src.domain.risk.position_sizer import PositionSizer
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderType,
    PositionSide,
)
from src.infrastructure.exchange.paper import PlaceOrderRequest
from src.shared.config.settings import get_settings
from src.shared.exceptions.domain import RiskViolationError, ValidationError
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.agent_service import AgentService
    from src.application.services.alert_service import AlertService
    from src.application.services.live_trading_service import LiveTradingService
    from src.application.services.market_data_service import MarketDataService
    from src.application.services.risk_service import RiskService
    from src.application.services.trading_service import TradingService
    from src.domain.agents.entities import CoordinatorDecision

logger = get_logger(__name__)

# Injected by DI: persists an executed order to the durable journal.
JournalCallback = Callable[["Order", "TradingService"], Awaitable[bool]]
LiveJournalCallback = Callable[["Order"], Awaitable[bool]]


class AutonomousBotConfig(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    interval_seconds: int = Field(default=60, ge=2, le=3600)
    execution_mode: str = "paper"  # "paper" or "real"
    confirm_real_trading: bool = False
    min_confidence_pct: float = Field(default=60.0, ge=10.0, le=99.0)
    risk_per_trade_pct: float = Field(default=2.0, ge=0.1, le=20.0)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    auto_execute: bool = True
    stop_loss_pct: float = Field(default=2.0, ge=0.5, le=15.0)
    take_profit_pct: float = Field(default=4.0, ge=0.5, le=30.0)

    @model_validator(mode="after")
    def validate_reward_risk_ratio(self) -> AutonomousBotConfig:
        if self.take_profit_pct < self.stop_loss_pct * 1.5:
            raise ValueError("take_profit_pct must be at least 1.5 times stop_loss_pct")
        return self


class AutonomousLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    level: str = "INFO"  # "INFO", "SUCCESS", "WARNING", "ERROR"
    cycle: int
    symbol: str
    message: str
    signal: str | None = None
    confidence_pct: float | None = None
    price: float | None = None
    order_id: str | None = None


class AutonomousBotStatus(BaseModel):
    is_running: bool
    config: AutonomousBotConfig
    total_cycles: int
    trades_executed: int
    started_at: datetime | None = None
    uptime_seconds: float = 0.0
    last_cycle_at: datetime | None = None
    last_price: float | None = None
    last_signal: str | None = None
    last_confidence: float | None = None
    last_error: str | None = None
    active_positions_count: int = 0
    logs: list[AutonomousLogEntry] = Field(default_factory=list)


class AutonomousEngine:
    """
    Autonomous Execution Engine running a continuous AI-guided market scanning and trading daemon.
    """

    def __init__(
        self,
        agent_service: AgentService | None = None,
        trading_service: TradingService | None = None,
        market_service: MarketDataService | None = None,
        risk_service: RiskService | None = None,
        journal: JournalCallback | None = None,
        live_trading_service: LiveTradingService | None = None,
        journal_real: LiveJournalCallback | None = None,
        alert_service: AlertService | None = None,
    ) -> None:
        self.agent_service = agent_service
        self.trading_service = trading_service
        self.market_service = market_service
        self.risk_service = risk_service
        self.journal = journal
        self.live_trading_service = live_trading_service
        self.journal_real = journal_real
        self.alert_service = alert_service

        self.is_running = False
        self.config = AutonomousBotConfig()
        self.total_cycles = 0
        self.trades_executed = 0
        self.started_at: datetime | None = None
        self.last_cycle_at: datetime | None = None
        self.last_price: float | None = None
        self.last_signal: str | None = None
        self.last_confidence: float | None = None
        self.last_error: str | None = None
        self._logs: list[AutonomousLogEntry] = []
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------

    def _mode(self) -> ExecutionMode:
        try:
            return ExecutionMode(self.config.execution_mode)
        except ValueError:
            return ExecutionMode.PAPER

    def _execution_service(self) -> TradingService | LiveTradingService | None:
        if self._mode() == ExecutionMode.REAL:
            return self.live_trading_service
        return self.trading_service

    async def _notify(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Best-effort alert (never breaks the trading loop)."""
        if self.alert_service is None:
            return
        try:
            await self.alert_service.notify(title, message, severity, metadata or {})
        except Exception as exc:  # pragma: no cover - alerting is best-effort
            logger.warning("autonomous_alert_failed", error=str(exc))

    def add_log(
        self,
        level: str,
        message: str,
        signal: str | None = None,
        confidence_pct: float | None = None,
        price: float | None = None,
        order_id: str | None = None,
    ) -> None:
        entry = AutonomousLogEntry(
            cycle=self.total_cycles,
            symbol=self.config.symbol,
            level=level,
            message=message,
            signal=signal,
            confidence_pct=confidence_pct,
            price=price,
            order_id=order_id,
        )
        self._logs.insert(0, entry)
        if len(self._logs) > 200:
            self._logs = self._logs[:200]

    def get_status(self) -> AutonomousBotStatus:
        uptime = 0.0
        if self.is_running and self.started_at:
            uptime = (datetime.now(tz=UTC) - self.started_at).total_seconds()

        positions_count = 0
        service = self._execution_service()
        if service:
            try:
                positions_count = len(service.get_positions(self._mode()))
            except Exception:
                positions_count = 0

        return AutonomousBotStatus(
            is_running=self.is_running,
            config=self.config,
            total_cycles=self.total_cycles,
            trades_executed=self.trades_executed,
            started_at=self.started_at,
            uptime_seconds=round(uptime, 1),
            last_cycle_at=self.last_cycle_at,
            last_price=self.last_price,
            last_signal=self.last_signal,
            last_confidence=self.last_confidence,
            last_error=self.last_error,
            active_positions_count=positions_count,
            logs=self._logs[:50],
        )

    async def start(self, config: AutonomousBotConfig | None = None) -> AutonomousBotStatus:
        if self.is_running:
            return self.get_status()

        if config:
            self.config = config

        mode = self._mode()
        if mode == ExecutionMode.REAL:
            settings = get_settings()
            if not settings.real_trading_enabled or not settings.enable_autonomous_real_trading:
                raise ValidationError(
                    "El bot autónomo en modo real está deshabilitado. Activa "
                    "Modo Real y 'Bot autónomo real' en Configuración > Control de ejecución."
                )
            if not self.config.confirm_real_trading:
                raise ValidationError(
                    "Confirma explícitamente el inicio de la sesión autónoma en modo real."
                )
            if self.live_trading_service is None:
                raise ValidationError("LiveTradingService is not wired into the engine.")

        self.is_running = True
        self.started_at = datetime.now(tz=UTC)
        self.last_error = None
        self.add_log(
            level="INFO",
            message=f"🚀 Autonomous Trading Daemon started for {self.config.symbol} ({self.config.timeframe}) every {self.config.interval_seconds}s.",
        )

        self.add_log(
            level="INFO",
            message=f"Execution mode: {mode.value.upper()}",
        )
        if mode == ExecutionMode.REAL:
            await self._notify(
                "Bot autónomo en modo REAL",
                f"El daemon operará con fondos reales en {self.config.symbol}.",
                AlertSeverity.CRITICAL,
                {"symbol": self.config.symbol, "mode": mode.value},
            )

        # Launch background loop
        self._task = asyncio.create_task(self._run_loop())
        return self.get_status()

    async def stop(self) -> AutonomousBotStatus:
        if not self.is_running:
            return self.get_status()

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        self.add_log(level="WARNING", message="🛑 Autonomous Trading Daemon stopped.")
        return self.get_status()

    async def _persist_execution(
        self,
        order: Order,
        mode: ExecutionMode | None = None,
    ) -> None:
        """Persist a bot execution via the injected journal callback (best-effort)."""
        mode = mode or self._mode()
        try:
            if mode == ExecutionMode.REAL:
                if self.journal_real is None or self.live_trading_service is None:
                    return
                await self.journal_real(order)
            else:
                if self.journal is None or self.trading_service is None:
                    return
                await self.journal(order, self.trading_service)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "autonomous_journal_persist_failed",
                order_id=str(order.id),
                error=str(exc),
            )

    async def emergency_kill(self) -> AutonomousBotStatus:
        """
        Immediately stops the daemon and closes all open positions.
        """
        await self.stop()
        closed_count = 0

        mode = self._mode()
        service = self._execution_service()

        if service:
            try:
                positions = service.get_positions(mode)
                for pos in positions:
                    # Prefer the last live tick; fall back to the position's own
                    # current price. Never invent a hardcoded price.
                    close_price = self.last_price
                    if close_price is None and pos.current_price > 0:
                        close_price = float(pos.current_price)
                    if close_price is None:
                        logger.error(
                            "emergency_kill_missing_price",
                            position_id=str(pos.id),
                        )
                        continue

                    if mode == ExecutionMode.REAL and self.live_trading_service is not None:
                        closed_order = await self.live_trading_service.close_position(
                            position_id=pos.id,
                            current_market_price=close_price,
                            mode=mode,
                            confirm=True,
                            exchange=pos.exchange,
                        )
                    elif self.trading_service is not None:
                        closed_order = self.trading_service.close_position(
                            position_id=pos.id,
                            current_market_price=close_price,
                            mode=ExecutionMode.PAPER,
                        )
                    else:
                        closed_order = None
                    if closed_order is not None:
                        await self._persist_execution(closed_order, mode)
                    closed_count += 1
            except Exception as e:
                logger.error("emergency_kill_failed", error=str(e))

        await self._notify(
            "Kill-switch de emergencia",
            f"Daemon detenido y {closed_count} posiciones cerradas en modo {mode.value}.",
            AlertSeverity.CRITICAL,
            {"closed_positions": closed_count, "mode": mode.value},
        )

        self.add_log(
            level="ERROR",
            message=f"🚨 EMERGENCY KILL-SWITCH ACTIVATED: Daemon halted and {closed_count} open positions closed.",
        )
        return self.get_status()

    async def _run_loop(self) -> None:
        """
        Main continuous autonomous cycle loop.
        """
        while self.is_running:
            try:
                await self._execute_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                logger.error("autonomous_cycle_error", error=str(e), exc_info=True)
                self.add_log(level="ERROR", message=f"Cycle evaluation error: {e!s}")

            try:
                await asyncio.sleep(self.config.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _execute_cycle(self) -> None:
        self.total_cycles += 1
        self.last_cycle_at = datetime.now(tz=UTC)
        symbol = self.config.symbol.replace("-", "/").replace("_", "/").upper()

        if not self.market_service:
            self.add_log(level="ERROR", message="MarketDataService not bound to AutonomousEngine")
            return

        # 1. Fetch current REAL-TIME price (ticker 'last'), not the last closed candle.
        #    A 1h candle only updates once per hour, so `candles[-1].close` would stay
        #    frozen while the chart keeps moving. We fall back to the candle close only
        #    if the ticker is temporarily unavailable, so the daemon never crashes.
        current_price: float | None = None
        try:
            current_price = float(await self.market_service.get_latest_price(symbol))
        except Exception as ticker_err:
            logger.warning(
                "ticker_price_unavailable_falling_back_to_candle",
                symbol=symbol,
                error=str(ticker_err),
            )

        if current_price is None:
            try:
                candles = await self.market_service.get_candles(
                    symbol=symbol,
                    timeframe=Timeframe(self.config.timeframe),
                    limit=100,
                )
                if not candles:
                    self.add_log(level="WARNING", message=f"No candles returned for {symbol}")
                    return

                current_price = float(candles[-1].close)
            except Exception as e:
                self.add_log(level="ERROR", message=f"Failed to fetch market data: {e}")
                return

        self.last_price = current_price

        # Keep open positions' unrealized PnL fresh with the latest tick.
        mode = self._mode()
        service = self._execution_service()
        if service:
            try:
                service.update_market_prices({symbol: current_price}, mode)
            except Exception as e:
                logger.warning("position_price_update_failed", error=str(e))

        # 2. Check existing open positions for Stop-Loss / Take-Profit triggers
        if service:
            try:
                open_positions = service.get_positions(mode)
                for pos in open_positions:
                    if pos.symbol == symbol:
                        # Evaluate TP/SL
                        triggered = False
                        reason = ""
                        if pos.side == PositionSide.LONG:
                            if pos.stop_loss and current_price <= float(pos.stop_loss):
                                triggered = True
                                reason = f"Stop-Loss triggered at ${current_price:,.2f} (SL: ${float(pos.stop_loss):,.2f})"
                            elif pos.take_profit and current_price >= float(pos.take_profit):
                                triggered = True
                                reason = f"Take-Profit triggered at ${current_price:,.2f} (TP: ${float(pos.take_profit):,.2f})"
                        elif pos.side == PositionSide.SHORT:
                            if pos.stop_loss and current_price >= float(pos.stop_loss):
                                triggered = True
                                reason = f"Stop-Loss triggered at ${current_price:,.2f} (SL: ${float(pos.stop_loss):,.2f})"
                            elif pos.take_profit and current_price <= float(pos.take_profit):
                                triggered = True
                                reason = f"Take-Profit triggered at ${current_price:,.2f} (TP: ${float(pos.take_profit):,.2f})"

                        if triggered:
                            if mode == ExecutionMode.REAL and self.live_trading_service is not None:
                                closed_order = await self.live_trading_service.close_position(
                                    position_id=pos.id,
                                    current_market_price=current_price,
                                    mode=mode,
                                    confirm=True,
                                    exchange=pos.exchange,
                                )
                            elif self.trading_service is not None:
                                closed_order = self.trading_service.close_position(
                                    position_id=pos.id,
                                    current_market_price=current_price,
                                    mode=ExecutionMode.PAPER,
                                )
                            else:
                                closed_order = None
                            if closed_order is not None:
                                await self._persist_execution(closed_order, mode)
                                if mode != ExecutionMode.REAL:
                                    await self._notify(
                                        "Salida automática (paper)",
                                        reason,
                                        AlertSeverity.WARNING,
                                        {"symbol": symbol, "reason": reason},
                                    )
                            self.add_log(
                                level="WARNING",
                                message=f"🛡️ Auto-Exit: {reason}",
                                price=current_price,
                            )
            except Exception as e:
                logger.warning("position_tracking_error", error=str(e))

        # 3. Multi-Agent Committee Consensus Evaluation
        if not self.agent_service:
            self.add_log(level="WARNING", message="AgentService not bound to AutonomousEngine")
            return

        # Start a fresh LLM cycle budget before the committee spends tokens.
        self.agent_service.begin_cycle()

        decision: CoordinatorDecision = await self.agent_service.run_analysis(
            symbol=symbol,
            timeframe=self.config.timeframe,
            lookback_candles=100,
        )

        signal_str = str(decision.final_signal.value)
        raw_conf = float(decision.aggregate_confidence)
        confidence_pct = raw_conf * 100.0 if raw_conf <= 1.0 else raw_conf

        self.last_signal = signal_str.upper()
        self.last_confidence = round(confidence_pct, 1)

        log_msg = (
            f"AI Committee Verdict: {signal_str.upper()} "
            f"(signal-strength score {confidence_pct:.1f}%, not success probability; "
            f"consensus {decision.consensus_level}) "
            f"@ ${current_price:,.2f}"
        )

        # 4. Check if signal meets execution threshold
        is_actionable = signal_str.upper() in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL")
        meets_confidence = confidence_pct >= self.config.min_confidence_pct

        if not decision.risk_approved:
            risk_reasons = (
                "; ".join(f"{flag.code}: {flag.description}" for flag in decision.all_risk_flags)
                or "El comité no aprobó la entrada."
            )
            self.add_log(
                level="WARNING",
                message=f"{log_msg} — Operación abstendida por Risk Manager: {risk_reasons}",
                signal=signal_str.upper(),
                confidence_pct=confidence_pct,
                price=current_price,
            )
            return

        if not is_actionable or not meets_confidence:
            self.add_log(
                level="INFO",
                message=f"{log_msg} — Standing by (No trade criteria met).",
                signal=signal_str.upper(),
                confidence_pct=confidence_pct,
                price=current_price,
            )
            return

        if not self.config.auto_execute:
            self.add_log(
                level="INFO",
                message=f"{log_msg} — Actionable signal detected, but Auto-Execute is disabled.",
                signal=signal_str.upper(),
                confidence_pct=confidence_pct,
                price=current_price,
            )
            return

        # 5. Order Sizing & Placement
        if not service:
            return

        try:
            portfolio = service.get_portfolio(mode)
            open_positions = service.get_positions(mode)
            order_side = OrderSide.BUY if "BUY" in signal_str.upper() else OrderSide.SELL

            if len(open_positions) >= self.config.max_open_positions:
                self.add_log(
                    level="WARNING",
                    message=f"{log_msg} — Max open positions limit ({self.config.max_open_positions}) reached. Order skipped.",
                    signal=signal_str.upper(),
                    confidence_pct=confidence_pct,
                    price=current_price,
                )
                return

            # Spot automation only manages positions it opened and tracks.
            # A SELL signal must never liquidate an unrelated wallet balance.
            symbol_position = next(
                (position for position in open_positions if position.symbol == symbol),
                None,
            )
            if order_side == OrderSide.SELL:
                if symbol_position is None:
                    self.add_log(
                        level="INFO",
                        message=(
                            f"{log_msg} — No hay posición del bot para cerrar; "
                            "se omite la venta para no tocar saldo externo."
                        ),
                        signal=signal_str.upper(),
                        confidence_pct=confidence_pct,
                        price=current_price,
                    )
                    return

                if mode == ExecutionMode.REAL and self.live_trading_service is not None:
                    closed_order = await self.live_trading_service.close_position(
                        position_id=symbol_position.id,
                        current_market_price=current_price,
                        mode=mode,
                        confirm=True,
                        exchange=symbol_position.exchange,
                    )
                elif self.trading_service is not None:
                    closed_order = self.trading_service.close_position(
                        position_id=symbol_position.id,
                        current_market_price=current_price,
                        mode=ExecutionMode.PAPER,
                    )
                else:
                    closed_order = None

                if closed_order is not None:
                    await self._persist_execution(closed_order, mode)
                    self.trades_executed += 1
                    self.add_log(
                        level="SUCCESS",
                        message=(
                            f"Salida por señal IA: {symbol} cerrada a "
                            f"${current_price:,.2f} ({signal_str.upper()})."
                        ),
                        signal=signal_str.upper(),
                        confidence_pct=confidence_pct,
                        price=current_price,
                        order_id=str(closed_order.id)[:8],
                    )
                    if mode != ExecutionMode.REAL:
                        await self._notify(
                            "Salida por señal IA (paper)",
                            f"Posición {symbol} cerrada por señal {signal_str.upper()}.",
                            AlertSeverity.INFO,
                            {"symbol": symbol, "mode": mode.value},
                        )
                return

            if symbol_position is not None:
                self.add_log(
                    level="INFO",
                    message=f"{log_msg} — Ya existe una posición del bot en {symbol}; se omite otra compra.",
                    signal=signal_str.upper(),
                    confidence_pct=confidence_pct,
                    price=current_price,
                )
                return

            # Size by loss at the stop, not by allocating risk% of equity as
            # notional. The shared RiskService profile also caps risk and
            # per-asset allocation; the Risk Guard in TradingService remains
            # the independent final preflight.
            balance = float(portfolio.total_balance)
            # Calculate SL and TP prices
            if order_side == OrderSide.BUY:
                stop_loss = round(current_price * (1 - self.config.stop_loss_pct / 100.0), 2)
                take_profit = round(current_price * (1 + self.config.take_profit_pct / 100.0), 2)
            else:
                stop_loss = round(current_price * (1 + self.config.stop_loss_pct / 100.0), 2)
                take_profit = round(current_price * (1 - self.config.take_profit_pct / 100.0), 2)

            sizing_request = PositionSizeRequest(
                symbol=symbol,
                account_balance=balance,
                risk_percentage=self.config.risk_per_trade_pct,
                entry_price=current_price,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
            )
            if self.risk_service is not None:
                sizing = self.risk_service.calculate_position_size(sizing_request)
            else:
                risk_settings = get_settings().risk
                sizing = PositionSizer.calculate(
                    sizing_request,
                    RiskProfile(
                        max_daily_loss_pct=float(risk_settings.daily_loss_pct),
                        max_drawdown_pct=float(risk_settings.max_drawdown_pct),
                        max_risk_per_trade_pct=float(risk_settings.max_risk_per_trade_pct),
                        max_position_size_pct=float(risk_settings.position_size_pct),
                        max_portfolio_exposure_pct=float(risk_settings.portfolio_exposure_pct),
                        max_open_positions=int(risk_settings.max_open_positions),
                    ),
                )
            quantity = round(sizing.recommended_units, 6)
            if not sizing.is_valid_setup or quantity <= 0:
                self.add_log(
                    level="WARNING",
                    message=(f"{log_msg} — Entrada omitida por sizing/riesgo: {sizing.notes}"),
                    signal=signal_str.upper(),
                    confidence_pct=confidence_pct,
                    price=current_price,
                )
                return

            # Real orders must fit the per-order notional cap. Sizing by risk
            # can produce a much larger notional than the operator allowed, so
            # shrink the quantity instead of letting the live service reject
            # every signal. The live service still enforces the cap as a hard
            # last line of defense.
            if mode == ExecutionMode.REAL:
                max_notional = float(get_settings().real_trading_max_notional)
                if current_price > 0 and quantity * current_price > max_notional:
                    capped_quantity = round(max_notional / current_price, 6)
                    self.add_log(
                        level="WARNING",
                        message=(
                            f"{log_msg} — Cantidad ajustada de {quantity} a {capped_quantity} "
                            f"{symbol} para respetar REAL_TRADING_MAX_NOTIONAL "
                            f"(${max_notional:,.2f})."
                        ),
                        signal=signal_str.upper(),
                        confidence_pct=confidence_pct,
                        price=current_price,
                    )
                    quantity = capped_quantity
                if quantity <= 0:
                    self.add_log(
                        level="WARNING",
                        message=(
                            f"{log_msg} — Entrada omitida: REAL_TRADING_MAX_NOTIONAL "
                            f"(${max_notional:,.2f}) no permite una cantidad positiva a "
                            f"${current_price:,.2f}."
                        ),
                        signal=signal_str.upper(),
                        confidence_pct=confidence_pct,
                        price=current_price,
                    )
                    return

            place_req = PlaceOrderRequest(
                symbol=symbol,
                side=order_side,
                order_type=OrderType.MARKET,
                quantity=quantity,
                price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                coordinator_decision_id=decision.id,
                client_order_id=f"ata-{decision.id.hex[:24]}",
            )

            if mode == ExecutionMode.REAL and self.live_trading_service is not None:
                executed_order = await self.live_trading_service.place_order(
                    req=place_req,
                    current_market_price=current_price,
                    confirm=True,
                )
            elif self.trading_service is not None:
                executed_order = self.trading_service.place_order(
                    req=place_req,
                    current_market_price=current_price,
                    mode=ExecutionMode.PAPER,
                )
            else:
                return
            await self._persist_execution(executed_order, mode)
            if mode != ExecutionMode.REAL:
                await self._notify(
                    "Orden ejecutada (paper)",
                    f"{order_side.value.upper()} {quantity} {symbol} @ ${current_price:,.2f}",
                    AlertSeverity.SUCCESS,
                    {
                        "symbol": symbol,
                        "order_id": str(executed_order.id),
                        "mode": mode.value,
                    },
                )

            self.trades_executed += 1
            self.add_log(
                level="SUCCESS",
                message=f"⚡ AUTO-ORDER EXECUTED: {order_side.value} {quantity} {symbol} @ ${current_price:,.2f} | SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f}",
                signal=signal_str.upper(),
                confidence_pct=confidence_pct,
                price=current_price,
                order_id=str(executed_order.id)[:8],
            )
            logger.info(
                "autonomous_order_placed",
                order_id=str(executed_order.id),
                symbol=symbol,
                side=order_side.value,
            )

        except RiskViolationError as e:
            self.add_log(
                level="WARNING",
                message=f"🛡️ Order blocked by Risk Guard: {e!s}",
                signal=signal_str.upper(),
                confidence_pct=confidence_pct,
                price=current_price,
            )
        except Exception as e:
            self.add_log(
                level="ERROR",
                message=f"Order execution error: {e!s}",
                signal=signal_str.upper(),
                confidence_pct=confidence_pct,
                price=current_price,
            )
