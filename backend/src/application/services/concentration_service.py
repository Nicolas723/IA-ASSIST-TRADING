"""
Read-only portfolio concentration and correlation report.

The pre-flight risk guard already enforces a per-asset notional cap and a gross
portfolio exposure cap. This service exposes the same measurements in a form an
operator can inspect, and adds a descriptive pairwise return-correlation matrix
for a configurable symbol universe.

It is strictly informational: it never blocks, resizes or routes orders, and a
correlation is a statistic about past returns, not a forecast or a probability.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.domain.trading.entities import ExecutionMode
from src.shared.config.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from src.application.services.live_trading_service import LiveTradingService
    from src.application.services.market_data_service import MarketDataService
    from src.application.services.risk_service import RiskService
    from src.application.services.trading_service import TradingService
    from src.domain.market.entities import Candle, Timeframe
    from src.domain.trading.entities import Portfolio

DISCLAIMER = (
    "Descriptive measurement of current exposure and past return correlations. "
    "It is not a forecast or a probability, and it does not authorize or block orders."
)

HIGH_CORRELATION_THRESHOLD = 0.8
MIN_CORRELATION_SAMPLES = 30


class SymbolExposure(BaseModel):
    symbol: str
    exposure_usd: float
    equity_pct: float | None = None
    limit_usd: float | None = None
    headroom_usd: float | None = None
    within_limit: bool | None = None


class CorrelationPair(BaseModel):
    left: str
    right: str
    correlation: float | None
    sample_size: int
    high: bool


class ConcentrationReport(BaseModel):
    generated_at: datetime
    mode: str
    timeframe: str
    lookback: int
    universe: list[str]
    equity_usd: float
    gross_exposure_usd: float
    gross_exposure_pct: float | None
    max_portfolio_exposure_pct: float
    max_position_size_pct: float
    positions: list[SymbolExposure] = Field(default_factory=list)
    correlations: list[CorrelationPair] = Field(default_factory=list)
    high_correlation_threshold: float = HIGH_CORRELATION_THRESHOLD
    warnings: list[str] = Field(default_factory=list)
    read_only: bool = True
    affects_trading: bool = False
    disclaimer: str = DISCLAIMER


def pearson_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson correlation of two equal-length series, or ``None`` if undefined."""
    if len(left) != len(right) or len(left) < 2:
        return None
    count = len(left)
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    covariance = sum(
        (left_value - mean_left) * (right_value - mean_right)
        for left_value, right_value in zip(left, right, strict=True)
    )
    variance_left = sum((value - mean_left) ** 2 for value in left)
    variance_right = sum((value - mean_right) ** 2 for value in right)
    if variance_left <= 0.0 or variance_right <= 0.0:
        return None
    return round(covariance / math.sqrt(variance_left * variance_right), 4)


def returns_by_timestamp(candles: Sequence[Candle]) -> dict[datetime, float]:
    """Simple returns keyed by candle timestamp, using closed candles only."""
    ordered = sorted(
        (candle for candle in candles if candle.is_closed),
        key=lambda candle: candle.timestamp,
    )
    returns: dict[datetime, float] = {}
    previous_close: float | None = None
    for candle in ordered:
        close = float(candle.close)
        if previous_close is not None and previous_close > 0.0:
            returns[candle.timestamp] = (close - previous_close) / previous_close
        previous_close = close
    return returns


def _correlations(
    universe: Sequence[str],
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    threshold: float,
) -> tuple[list[CorrelationPair], list[str]]:
    returns = {
        symbol: returns_by_timestamp(candles_by_symbol.get(symbol, [])) for symbol in universe
    }
    pairs: list[CorrelationPair] = []
    warnings: list[str] = []
    for index, left in enumerate(universe):
        for right in universe[index + 1 :]:
            shared = sorted(set(returns[left]).intersection(returns[right]))
            left_series = [returns[left][timestamp] for timestamp in shared]
            right_series = [returns[right][timestamp] for timestamp in shared]
            correlation = (
                pearson_correlation(left_series, right_series)
                if len(shared) >= MIN_CORRELATION_SAMPLES
                else None
            )
            high = correlation is not None and abs(correlation) >= threshold
            pairs.append(
                CorrelationPair(
                    left=left,
                    right=right,
                    correlation=correlation,
                    sample_size=len(shared),
                    high=high,
                )
            )
            if correlation is None:
                warnings.append(
                    f"No hay muestras suficientes para correlacionar {left} y {right} "
                    f"({len(shared)} velas comunes; mínimo {MIN_CORRELATION_SAMPLES})."
                )
    return pairs, warnings


def build_concentration_report(
    *,
    portfolio: Portfolio,
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    universe: Sequence[str],
    timeframe: str,
    lookback: int,
    mode: str,
    max_position_size_pct: float,
    max_portfolio_exposure_pct: float,
    high_correlation_threshold: float = HIGH_CORRELATION_THRESHOLD,
    now: datetime | None = None,
) -> ConcentrationReport:
    """Build a descriptive report from an existing portfolio snapshot."""
    equity = float(portfolio.total_balance)
    gross_exposure = float(portfolio.gross_exposure_usd)
    gross_exposure_pct = round(gross_exposure / equity * 100.0, 2) if equity > 0 else None
    limit_usd = equity * (max_position_size_pct / 100.0) if equity > 0 else None

    symbols_with_exposure = sorted({position.symbol for position in portfolio.positions})
    positions: list[SymbolExposure] = []
    for symbol in symbols_with_exposure:
        exposure = float(portfolio.exposure_usd(symbol))
        equity_pct = round(exposure / equity * 100.0, 2) if equity > 0 else None
        headroom = round(max(0.0, limit_usd - exposure), 2) if limit_usd is not None else None
        positions.append(
            SymbolExposure(
                symbol=symbol,
                exposure_usd=round(exposure, 2),
                equity_pct=equity_pct,
                limit_usd=round(limit_usd, 2) if limit_usd is not None else None,
                headroom_usd=headroom,
                within_limit=(exposure <= limit_usd) if limit_usd is not None else None,
            )
        )
    positions.sort(key=lambda item: item.exposure_usd, reverse=True)

    correlations, warnings = _correlations(universe, candles_by_symbol, high_correlation_threshold)

    if gross_exposure_pct is not None and gross_exposure_pct > max_portfolio_exposure_pct:
        warnings.append(
            f"La exposición bruta ({gross_exposure_pct}%) supera el límite de cartera "
            f"({max_portfolio_exposure_pct}%)."
        )
    for position in positions:
        if position.within_limit is False:
            warnings.append(
                f"{position.symbol} concentra {position.equity_pct}% del capital y supera "
                f"el límite por activo ({max_position_size_pct}%)."
            )

    exposed = {position.symbol for position in positions}
    for pair in correlations:
        if pair.high and pair.left in exposed and pair.right in exposed:
            warnings.append(
                f"{pair.left} y {pair.right} tienen correlación {pair.correlation} "
                f"(umbral {high_correlation_threshold}); su exposición conjunta no está diversificada."
            )
    if len(universe) < 2:
        warnings.append(
            "La correlación necesita al menos dos símbolos; pasa "
            "`symbols=BTC/USDT,ETH/USDT` para calcularla."
        )

    return ConcentrationReport(
        generated_at=now or datetime.now(tz=UTC),
        mode=mode,
        timeframe=timeframe,
        lookback=lookback,
        universe=list(universe),
        equity_usd=round(equity, 2),
        gross_exposure_usd=round(gross_exposure, 2),
        gross_exposure_pct=gross_exposure_pct,
        max_portfolio_exposure_pct=max_portfolio_exposure_pct,
        max_position_size_pct=max_position_size_pct,
        positions=positions,
        correlations=correlations,
        high_correlation_threshold=high_correlation_threshold,
        warnings=warnings,
    )


def normalize_universe(symbols: Sequence[str] | None, fallback: Sequence[str]) -> list[str]:
    """Normalize, deduplicate and fall back to the configured trading allowlist."""
    normalized: list[str] = []
    for raw in symbols or fallback:
        symbol = raw.strip().upper().replace("-", "/").replace("_", "/")
        if symbol and "/" in symbol and symbol not in normalized:
            normalized.append(symbol)
    return normalized


class ConcentrationService:
    """Read the active portfolio and build a concentration report."""

    def __init__(
        self,
        *,
        market_service: MarketDataService,
        risk_service: RiskService,
        trading_service: TradingService,
        live_trading_service: LiveTradingService,
    ) -> None:
        self._market = market_service
        self._risk = risk_service
        self._trading = trading_service
        self._live = live_trading_service

    async def analyze(
        self,
        *,
        symbols: Sequence[str] | None = None,
        timeframe: Timeframe,
        lookback: int = 200,
        now: datetime | None = None,
    ) -> ConcentrationReport:
        settings = get_settings()
        mode = settings.execution_mode
        mode_value = mode.value
        universe = normalize_universe(symbols, settings.real_trading_allowed_symbols)
        if not universe:
            universe = ["BTC/USDT"]

        if mode_value == ExecutionMode.REAL.value:
            portfolio = self._live.get_portfolio(ExecutionMode.REAL)
        else:
            portfolio = self._trading.get_portfolio(ExecutionMode.PAPER)

        candles_by_symbol: dict[str, Sequence[Candle]] = {}
        for symbol in universe:
            try:
                candles_by_symbol[symbol] = await self._market.get_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=lookback + 1,
                )
            except Exception:
                candles_by_symbol[symbol] = []

        profile = self._risk.guard.profile
        return build_concentration_report(
            portfolio=portfolio,
            candles_by_symbol=candles_by_symbol,
            universe=universe,
            timeframe=timeframe.value,
            lookback=lookback,
            mode=mode_value,
            max_position_size_pct=profile.max_position_size_pct,
            max_portfolio_exposure_pct=profile.max_portfolio_exposure_pct,
            now=now,
        )
