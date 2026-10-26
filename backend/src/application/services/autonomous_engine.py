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

from pydantic import BaseModel, Field, field_validator, model_validator

from src.application.services.correlation_guard import (
    CorrelationGuardDecision,
    evaluate_correlation_guard,
)
from src.application.services.market_prescan_service import (
    MarketPreScanService,
    PreScanResult,
    normalize_symbol,
)
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
    from src.domain.trading.entities import Position

logger = get_logger(__name__)

# Injected by DI: persists an executed order to the durable journal.
JournalCallback = Callable[["Order", "TradingService"], Awaitable[bool]]
LiveJournalCallback = Callable[["Order"], Awaitable[bool]]


class AutonomousBotConfig(BaseModel):
    symbol: str = "BTC/USDT"
    # Markets considered by the deterministic pre-scan. The primary `symbol` is
    # always included. An empty list means "use SHADOW_SCAN_SYMBOLS" when the
    # session starts; the engine then analyzes only the best allowed market.
    scan_symbols: list[str] = Field(default_factory=list, max_length=10)
    timeframe: str = "1h"
    interval_seconds: int = Field(default=60, ge=2, le=3600)
    # Minimum seconds between full LLM committee runs. Position tracking and
    # SL/TP exits still run every `interval_seconds`; only the expensive entry
    # analysis is throttled so a free-tier request quota lasts longer.
    llm_analysis_interval_seconds: int = Field(default=1800, ge=0, le=86400)
    execution_mode: str = "paper"  # "paper" or "real"
    confirm_real_trading: bool = False
    min_confidence_pct: float = Field(default=60.0, ge=10.0, le=99.0)
    risk_per_trade_pct: float = Field(default=2.0, ge=0.1, le=20.0)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    auto_execute: bool = True
    stop_loss_pct: float = Field(default=2.0, ge=0.5, le=15.0)
    take_profit_pct: float = Field(default=4.0, ge=0.5, le=30.0)

    @field_validator("symbol")
    @classmethod
    def normalize_primary_symbol(cls, value: str) -> str:
        symbol = normalize_symbol(value)
        if "/" not in symbol:
            raise ValueError(f"Símbolo inválido: {value!r}. Usa formato BASE/QUOTE.")
        return symbol

    @field_validator("scan_symbols")
    @classmethod
    def normalize_scan_symbols(cls, values: list[str]) -> list[str]:
        symbols: list[str] = []
        for raw in values:
            symbol = normalize_symbol(raw)
            if not symbol:
                continue
            if "/" not in symbol:
                raise ValueError(f"Símbolo inválido: {raw!r}. Usa formato BASE/QUOTE.")
            if symbol not in symbols:
                symbols.append(symbol)
        return symbols

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
    last_analysis_at: datetime | None = None
    last_price: float | None = None
    last_symbol: str | None = None
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
        self.last_symbol: str | None = None
        self.last_signal: str | None = None
        self.last_confidence: float | None = None
        self.last_error: str | None = None
        self._last_analysis_at: datetime | None = None
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

    def _resolve_scan_universe(self, requested: list[str]) -> list[str]:
        """Normalize the requested universe and always include the primary symbol.

        An empty request means "use the configured shadow-scan universe". Passing
        the primary symbol alone keeps the legacy single-market behaviour and
        avoids extra market-data calls.
        """
        raw = list(requested)
        if not raw:
            raw = [self.config.symbol, *get_settings().shadow_scan_symbols]
        universe: list[str] = []
        for item in raw:
            symbol = normalize_symbol(item)
            if "/" not in symbol:
                continue
            if symbol not in universe:
                universe.append(symbol)
        if self.config.symbol not in universe:
            universe.insert(0, self.config.symbol)
        return universe[:10]

    def _allowed_symbols(self) -> set[str]:
        """Symbols eligible for entry analysis in the current execution mode."""
        if self._mode() != ExecutionMode.REAL:
            return set(self.config.scan_symbols or [self.config.symbol])
        raw = getattr(get_settings(), "real_trading_allowed_symbols", None)
        if raw is None:
            # Conservative fallback: never widen execution when the setting is
            # missing from an injected/test configuration.
            return {self.config.symbol}
        return {normalize_symbol(symbol) for symbol in raw}

    async def _latest_price(self, symbol: str) -> float | None:
        """Return the real-time ticker price, falling back to the last candle."""
        if self.market_service is None:
            return None
        try:
            return float(await self.market_service.get_latest_price(symbol))
        except Exception as exc:
            logger.warning(
                "ticker_price_unavailable_falling_back_to_candle",
                symbol=symbol,
                error=str(exc),
            )
        try:
            candles = await self.market_service.get_candles(
                symbol=symbol,
                timeframe=Timeframe(self.config.timeframe),
                limit=100,
            )
            if not candles:
                return None
            return float(candles[-1].close)
        except Exception as exc:
            logger.warning("candle_price_unavailable", symbol=symbol, error=str(exc))
            return None

    async def _pre_scan(self, universe: list[str]) -> PreScanResult | None:
        """Run the deterministic multi-market ranking (never raises)."""
        if self.market_service is None or len(universe) < 2:
            return None
        try:
            result = await MarketPreScanService(self.market_service).scan(
                universe,
                timeframe=self.config.timeframe,
                lookback_candles=24,
            )
        except Exception as exc:
            logger.warning("autonomous_prescan_failed", error=str(exc))
            self.add_log(
                level="WARNING",
                message=f"No se pudo completar el escaneo multimercado: {exc}",
            )
            return None

        observed = [candidate for candidate in result.candidates if candidate.rank is not None]
        if not observed:
            self.add_log(
                level="WARNING",
                message=(
                    f"Escaneo de {len(result.candidates)} mercados sin datos "
                    "suficientes; se analiza el símbolo principal."
                ),
            )
            return result

        ordered = result.ranked
        summary = " · ".join(
            f"{candidate.symbol} {candidate.lookback_return_pct:+.2f}% "
            f"({candidate.trend_regime or 'sin régimen'})"
            for candidate in ordered
            if candidate.lookback_return_pct is not None
        )
        self.add_log(
            level="INFO",
            message=(
                f"🔎 Escaneo {len(result.candidates)} mercados ({result.timeframe}): {summary}."
            ),
            symbol=ordered[0].symbol,
        )
        return result

    @staticmethod
    def _exit_reason(pos: Position, price: float) -> tuple[bool, str]:
        """Return whether a position hit its local stop-loss or take-profit."""
        if pos.side == PositionSide.LONG:
            if pos.stop_loss and price <= float(pos.stop_loss):
                return True, (
                    f"Stop-Loss triggered at ${price:,.2f} (SL: ${float(pos.stop_loss):,.2f})"
                )
            if pos.take_profit and price >= float(pos.take_profit):
                return True, (
                    f"Take-Profit triggered at ${price:,.2f} (TP: ${float(pos.take_profit):,.2f})"
                )
        elif pos.side == PositionSide.SHORT:
            if pos.stop_loss and price >= float(pos.stop_loss):
                return True, (
                    f"Stop-Loss triggered at ${price:,.2f} (SL: ${float(pos.stop_loss):,.2f})"
                )
            if pos.take_profit and price <= float(pos.take_profit):
                return True, (
                    f"Take-Profit triggered at ${price:,.2f} (TP: ${float(pos.take_profit):,.2f})"
                )
        return False, ""

    async def _correlation_guard_for(
        self,
        *,
        symbol: str,
        open_positions: list[Position],
    ) -> CorrelationGuardDecision:
        """Measure positive correlation with the other open positions.

        The guard is best-effort: missing market data keeps the requested size
        and is reported, because it is a diversification control and not the
        last line of defense. It only reduces or blocks new long exposure.
        """
        settings = get_settings()
        if not settings.correlation_guard_enabled:
            return CorrelationGuardDecision(
                action="allow",
                size_multiplier=1.0,
                max_correlation=None,
                correlated_symbol=None,
                sample_size=0,
                reason="Correlation guard disabled.",
            )

        other_symbols = sorted(
            {position.symbol for position in open_positions if position.symbol != symbol}
        )
        if not other_symbols:
            return CorrelationGuardDecision(
                action="allow",
                size_multiplier=1.0,
                max_correlation=None,
                correlated_symbol=None,
                sample_size=0,
                reason="No other open position to compare.",
            )
        if self.market_service is None:
            return CorrelationGuardDecision(
                action="unavailable",
                size_multiplier=1.0,
                max_correlation=None,
                correlated_symbol=None,
                sample_size=0,
                reason="Market data service unavailable.",
            )

        timeframe = Timeframe(self.config.timeframe)
        limit = int(settings.correlation_lookback) + 1
        try:
            candidate_candles = await self.market_service.get_candles(
                symbol=symbol, timeframe=timeframe, limit=limit
            )
            existing_candles = {}
            for other in other_symbols:
                existing_candles[other] = await self.market_service.get_candles(
                    symbol=other, timeframe=timeframe, limit=limit
                )
        except Exception as exc:
            logger.warning(
                "correlation_guard_data_unavailable",
                symbol=symbol,
                positions=other_symbols,
                error=str(exc),
            )
            return CorrelationGuardDecision(
                action="unavailable",
                size_multiplier=1.0,
                max_correlation=None,
                correlated_symbol=None,
                sample_size=0,
                reason=f"No se pudieron cargar las velas para correlacionar: {exc}",
            )

        return evaluate_correlation_guard(
            candidate_symbol=symbol,
            candidate_candles=candidate_candles,
            existing_candles=existing_candles,
            reduce_threshold=float(settings.correlation_reduce_threshold),
            block_threshold=float(settings.correlation_block_threshold),
            min_samples=int(settings.correlation_min_samples),
        )

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
        symbol: str | None = None,
    ) -> None:
        entry = AutonomousLogEntry(
            cycle=self.total_cycles,
            symbol=symbol or self.last_symbol or self.config.symbol,
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
            last_analysis_at=self._last_analysis_at,
            last_price=self.last_price,
            last_symbol=self.last_symbol,
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

        # Resolve the scan universe once, at session start, so the status
        # endpoint shows exactly what the daemon will scan. An empty list falls
        # back to the configured shadow-scan universe; the primary symbol is
        # always included.
        self.config.scan_symbols = self._resolve_scan_universe(self.config.scan_symbols)

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
        self.last_symbol = None
        self._last_analysis_at = None
        self.add_log(
            level="INFO",
            message=f"🚀 Autonomous Trading Daemon started for {self.config.symbol} ({self.config.timeframe}) every {self.config.interval_seconds}s.",
        )
        self.add_log(
            level="INFO",
            message=(
                f"🔎 Multi-market scan universe ({len(self.config.scan_symbols)}): "
                f"{', '.join(self.config.scan_symbols)}. One candidate is sent to "
                "the committee per analysis; execution still requires the allowlist."
            ),
        )
        if mode == ExecutionMode.REAL:
            allowed = sorted(self._allowed_symbols())
            self.add_log(
                level="INFO",
                message=(
                    f"Allowlist REAL activa: {', '.join(allowed) or 'ninguna'}. "
                    f"Analizables: "
                    f"{', '.join(s for s in self.config.scan_symbols if s in allowed) or 'ninguno'}."
                ),
            )
        if self.config.llm_analysis_interval_seconds > 0:
            self.add_log(
                level="INFO",
                message=(
                    "LLM committee analysis throttled to every "
                    f"{self.config.llm_analysis_interval_seconds}s; "
                    "position tracking still runs every cycle."
                ),
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
                    # Prefer the position's own last mark; never close an ETH
                    # position at a BTC price in a multi-market session.
                    close_price: float | None = (
                        float(pos.current_price) if pos.current_price > 0 else None
                    )
                    if close_price is None and pos.symbol == self.last_symbol:
                        close_price = self.last_price
                    if close_price is None:
                        logger.error(
                            "emergency_kill_missing_price",
                            position_id=str(pos.id),
                            symbol=pos.symbol,
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
        primary_symbol = self.config.symbol
        universe = self.config.scan_symbols or [primary_symbol]

        if not self.market_service:
            self.add_log(level="ERROR", message="MarketDataService not bound to AutonomousEngine")
            return

        # 1. Deterministic multi-market pre-scan. The expensive LLM committee
        #    only runs on the best candidate that the execution allowlist
        #    permits; every other market is still measured and logged.
        symbol = primary_symbol
        scan_result = await self._pre_scan(universe)
        allowed = self._allowed_symbols()
        if scan_result is not None:
            eligible = [
                candidate for candidate in scan_result.ranked if candidate.symbol in allowed
            ]
            if eligible:
                chosen = eligible[0]
                symbol = chosen.symbol
                self.add_log(
                    level="INFO",
                    message=(
                        f"Candidato elegido: {symbol} "
                        f"(score {chosen.selection_score:+.2f}; "
                        f"{chosen.lookback_return_pct:+.2f}% en "
                        f"{scan_result.lookback_candles} velas; "
                        f"{chosen.trend_regime or 'sin régimen'}). "
                        f"Elegibles por allowlist: {len(eligible)}/{len(scan_result.ranked)}."
                    ),
                    symbol=symbol,
                )
            else:
                self.add_log(
                    level="WARNING",
                    message=(
                        f"Ningún mercado observado está en la allowlist de ejecución "
                        f"({', '.join(sorted(allowed)) or 'vacía'}); "
                        f"se analiza {primary_symbol}."
                    ),
                    symbol=primary_symbol,
                )

        # 2. Fetch the selected market's REAL-TIME price (ticker 'last'), not
        #    the last closed candle. A 1h candle only updates once per hour, so
        #    `candles[-1].close` would stay frozen while the chart keeps moving.
        current_price = await self._latest_price(symbol)
        if current_price is None:
            self.add_log(
                level="WARNING",
                message=f"No candles returned for {symbol}",
                symbol=symbol,
            )
        else:
            self.last_price = current_price
            self.last_symbol = symbol

        # 3. Refresh every open position, not only the analyzed symbol. A
        #    multi-market session can hold ETH while the committee looks at BTC.
        mode = self._mode()
        service = self._execution_service()
        open_positions: list[Position] = []
        price_map: dict[str, float] = {}
        if current_price is not None:
            price_map[symbol] = current_price
        if service:
            try:
                open_positions = list(service.get_positions(mode))
            except Exception as exc:
                logger.warning("position_list_failed", error=str(exc))
                open_positions = []

            for pos in open_positions:
                if pos.symbol in price_map:
                    continue
                tick: float | None = None
                try:
                    tick = float(await self.market_service.get_latest_price(pos.symbol))
                except Exception as exc:
                    logger.debug(
                        "position_ticker_unavailable",
                        symbol=pos.symbol,
                        error=str(exc),
                    )
                if tick is None or tick <= 0:
                    try:
                        mark = float(pos.current_price)
                    except (TypeError, ValueError):
                        mark = 0.0
                    tick = mark if mark > 0 else None
                if tick is not None:
                    price_map[pos.symbol] = tick

            try:
                service.update_market_prices(price_map, mode)
            except Exception as exc:
                logger.warning("position_price_update_failed", error=str(exc))

        # 4. Check every open position for Stop-Loss / Take-Profit triggers
        #    using that position's own market price.
        for pos in open_positions:
            pos_price = price_map.get(pos.symbol)
            if pos_price is None:
                logger.warning(
                    "position_price_missing",
                    position_id=str(pos.id),
                    symbol=pos.symbol,
                )
                continue

            triggered, reason = self._exit_reason(pos, pos_price)
            if not triggered:
                continue

            try:
                if mode == ExecutionMode.REAL and self.live_trading_service is not None:
                    closed_order = await self.live_trading_service.close_position(
                        position_id=pos.id,
                        current_market_price=pos_price,
                        mode=mode,
                        confirm=True,
                        exchange=pos.exchange,
                    )
                elif self.trading_service is not None:
                    closed_order = self.trading_service.close_position(
                        position_id=pos.id,
                        current_market_price=pos_price,
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
                            {"symbol": pos.symbol, "reason": reason},
                        )
                self.add_log(
                    level="WARNING",
                    message=f"🛡️ Auto-Exit: {reason}",
                    price=pos_price,
                    symbol=pos.symbol,
                )
            except Exception as exc:
                logger.warning(
                    "position_exit_failed",
                    position_id=str(pos.id),
                    symbol=pos.symbol,
                    error=str(exc),
                )

        # Entry analysis is the only part gated by the execution allowlist.
        # Position tracking above already ran, so a disallowed market can never
        # trap an open position.
        if symbol not in allowed:
            self.add_log(
                level="WARNING",
                message=(
                    f"{symbol} no está en la allowlist de ejecución; se omite el "
                    "análisis de entrada y solo se siguen las posiciones abiertas."
                ),
                symbol=symbol,
            )
            return
        if current_price is None:
            return

        # 5. Multi-Agent Committee Consensus Evaluation
        if not self.agent_service:
            self.add_log(level="WARNING", message="AgentService not bound to AutonomousEngine")
            return

        # Throttle the expensive LLM committee. Exits and SL/TP tracking already
        # ran above, so skipping only delays new entry analysis. This stretches a
        # free-tier request quota across the day instead of burning it in an hour.
        throttle = self.config.llm_analysis_interval_seconds
        if throttle > 0 and self._last_analysis_at is not None:
            elapsed = (datetime.now(tz=UTC) - self._last_analysis_at).total_seconds()
            if elapsed < throttle:
                logger.debug(
                    "committee_analysis_deferred",
                    elapsed_seconds=int(elapsed),
                    next_in_seconds=int(throttle - elapsed),
                )
                return

        self._last_analysis_at = datetime.now(tz=UTC)

        # AgentService.run_analysis starts a fresh per-cycle LLM budget itself,
        # so both the engine and the manual /agents/analyze endpoint get one.
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

        # 6. Check if signal meets execution threshold
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

        # 7. Order Sizing & Placement
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

            # Correlation-aware sizing: spot automation is long-only, so a new
            # long in a symbol that has been moving with an existing holding
            # adds concentration instead of diversification. Only positive
            # correlation is penalised; the guard never creates a signal.
            correlation_decision = await self._correlation_guard_for(
                symbol=symbol,
                open_positions=open_positions,
            )
            if correlation_decision.blocks:
                self.add_log(
                    level="WARNING",
                    message=f"{log_msg} — Entrada bloqueada por correlación: {correlation_decision.reason}",
                    signal=signal_str.upper(),
                    confidence_pct=confidence_pct,
                    price=current_price,
                )
                return
            if correlation_decision.action == "reduce":
                self.add_log(
                    level="INFO",
                    message=f"{log_msg} — {correlation_decision.reason}",
                    signal=signal_str.upper(),
                    confidence_pct=confidence_pct,
                    price=current_price,
                )
            elif correlation_decision.action == "unavailable":
                logger.info(
                    "correlation_guard_unavailable",
                    symbol=symbol,
                    reason=correlation_decision.reason,
                )

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
                current_symbol_exposure_usd=float(portfolio.exposure_usd(symbol)),
                current_total_exposure_usd=float(portfolio.gross_exposure_usd),
                correlation_multiplier=correlation_decision.size_multiplier,
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

            # Fail-closed preflight: never open new real risk while the exchange
            # cannot confirm that the local ledger is still trustworthy. Exits
            # above are intentionally not gated, so a degraded reconciliation
            # can never trap an open position.
            if mode == ExecutionMode.REAL and order_side == OrderSide.BUY:
                if self.live_trading_service is None:
                    self.add_log(
                        level="ERROR",
                        message=f"{log_msg} — Entrada omitida: falta el servicio de trading real.",
                        signal=signal_str.upper(),
                        confidence_pct=confidence_pct,
                        price=current_price,
                    )
                    return
                reconciled, gate_error = await self.live_trading_service.preflight_reconciliation()
                if not reconciled:
                    self.add_log(
                        level="ERROR",
                        message=(
                            f"{log_msg} — Entrada suspendida: falló la conciliación con el "
                            f"exchange ({gate_error or 'error desconocido'}). No se abre "
                            "riesgo nuevo hasta que la conciliación vuelva a responder."
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
