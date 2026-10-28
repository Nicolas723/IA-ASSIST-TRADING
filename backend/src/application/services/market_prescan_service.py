"""Deterministic multi-market pre-scan used to choose what to analyze.

The autonomous engine can only run the expensive LLM committee on one market
per cycle. This service ranks a small, explicitly supplied universe using only
closed candles so the committee spends its limited budget on the market with
the most relevant recent behaviour.

It never calls an LLM, sizes a position or places an order. The score is a
selection heuristic, not an expected return, a probability or an instruction
to trade. Execution remains governed by the allowlist and the Risk Guard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from src.application.services.regime_service import classify_regime
from src.domain.market.entities import Timeframe
from src.shared.config.settings import MAX_SCAN_SYMBOLS

if TYPE_CHECKING:
    from src.application.services.market_data_service import MarketDataService

SELECTION_BASIS = (
    "Recent closed-candle return over the lookback window plus a small "
    "trend-regime adjustment. It ranks which market to analyze; it is not an "
    "expected return, a success probability or an order instruction."
)
# Maximum points the trend label can add or subtract. Keeping it small means
# recent movement dominates while a confirmed trend breaks near-ties.
REGIME_WEIGHT_PCT = 0.5


def _as_utc(value: datetime) -> datetime:
    """Normalize exchange/DB timestamps before comparing with UTC wall time."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_symbol(value: str) -> str:
    """Normalize ``btc-usdt``/``BTC_USDT`` to ``BTC/USDT``."""
    return value.strip().upper().replace("-", "/").replace("_", "/")


class PreScanCandidate(BaseModel):
    """A deterministic snapshot of one market in the scan universe."""

    symbol: str
    status: str  # observed | insufficient_data | invalid_data | unavailable
    rank: int | None = None
    last_price: float | None = None
    lookback_return_pct: float | None = None
    trend_regime: str | None = None
    volatility_regime: str | None = None
    trend_strength: float | None = None
    regime_status: str = "insufficient_data"
    data_age_seconds: float | None = None
    selection_score: float | None = None
    error: str | None = None


class PreScanResult(BaseModel):
    timeframe: str
    scanned_at: datetime
    lookback_candles: int
    candidates: list[PreScanCandidate]
    selection_basis: str = SELECTION_BASIS
    execution_enabled: bool = False

    @property
    def ranked(self) -> list[PreScanCandidate]:
        """Observed candidates in selection order (best first)."""
        return sorted(
            (candidate for candidate in self.candidates if candidate.rank is not None),
            key=lambda candidate: candidate.rank or MAX_SCAN_SYMBOLS + 1,
        )


class MarketPreScanService:
    """Rank a bounded market universe without side effects."""

    def __init__(self, market_service: MarketDataService) -> None:
        self.market_service = market_service

    async def scan(
        self,
        symbols: list[str],
        timeframe: str = "1h",
        lookback_candles: int = 24,
        now: datetime | None = None,
    ) -> PreScanResult:
        tf = Timeframe(timeframe)
        if lookback_candles < 2 or lookback_candles > 100:
            raise ValueError("lookback_candles must be between 2 and 100.")

        normalized: list[str] = []
        for raw in symbols:
            symbol = normalize_symbol(raw)
            if not symbol:
                continue
            if "/" not in symbol:
                raise ValueError(f"Invalid symbol {raw!r}; use BASE/QUOTE notation.")
            if symbol not in normalized:
                normalized.append(symbol)
        if not normalized or len(normalized) > MAX_SCAN_SYMBOLS:
            raise ValueError(f"Pre-scan accepts between 1 and {MAX_SCAN_SYMBOLS} unique symbols.")

        reference = now or datetime.now(tz=UTC)
        candidates: list[PreScanCandidate] = []
        # MarketDataService shares the request's AsyncSession. Scan sequentially
        # to avoid concurrent operations on one SQLAlchemy session and to keep
        # exchange request bursts bounded.
        for symbol in normalized:
            try:
                candidate = await self._scan_one(symbol, tf, lookback_candles, reference)
            except Exception:
                candidate = PreScanCandidate(
                    symbol=symbol,
                    status="unavailable",
                    error="Market data could not be retrieved or validated.",
                )
            candidates.append(candidate)

        ranked = sorted(
            (candidate for candidate in candidates if candidate.selection_score is not None),
            key=lambda candidate: (
                candidate.selection_score if candidate.selection_score is not None else 0.0,
                candidate.symbol,
            ),
            reverse=True,
        )
        for rank, candidate in enumerate(ranked, start=1):
            candidate.rank = rank

        return PreScanResult(
            timeframe=tf.value,
            scanned_at=reference,
            lookback_candles=lookback_candles,
            candidates=candidates,
        )

    async def _scan_one(
        self,
        symbol: str,
        timeframe: Timeframe,
        lookback_candles: int,
        reference: datetime,
    ) -> PreScanCandidate:
        # Ask for enough history to label the regime (EMA slow + ATR
        # percentile) while keeping the movement window bounded.
        candles = await self.market_service.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=max(lookback_candles + 1, 120),
        )
        closed = [
            candle
            for candle in candles
            if _as_utc(candle.timestamp) + timedelta(seconds=timeframe.seconds) <= reference
        ]
        if len(closed) < 2:
            return PreScanCandidate(
                symbol=symbol,
                status="insufficient_data",
                error="At least two fully closed candles are required.",
            )

        ordered = sorted(closed, key=lambda candle: _as_utc(candle.timestamp))
        window = ordered[-lookback_candles:]
        first_close = window[0].close
        latest = window[-1]
        if first_close <= 0:
            return PreScanCandidate(
                symbol=symbol,
                status="invalid_data",
                error="The first close price must be positive.",
            )

        change_pct = float((latest.close - first_close) / first_close * 100)
        regime = classify_regime(
            ordered,
            symbol=symbol,
            timeframe=timeframe.value,
            now=reference,
            stale_after_seconds=int(timeframe.seconds * 2.5),
        )
        trend_strength = regime.metrics.trend_strength
        score = change_pct + _regime_adjustment(regime.trend, trend_strength)
        latest_close_at = _as_utc(latest.timestamp) + timedelta(seconds=timeframe.seconds)
        data_age = max(0.0, (reference - latest_close_at).total_seconds())
        if data_age > timeframe.seconds * 2.5:
            return PreScanCandidate(
                symbol=symbol,
                status="stale_data",
                last_price=float(latest.close),
                lookback_return_pct=round(change_pct, 4),
                data_age_seconds=round(data_age, 1),
                error="The last closed candle is older than 2.5 timeframes.",
            )

        return PreScanCandidate(
            symbol=symbol,
            status="observed",
            last_price=float(latest.close),
            lookback_return_pct=round(change_pct, 4),
            trend_regime=regime.trend,
            volatility_regime=regime.volatility,
            trend_strength=trend_strength,
            regime_status=regime.status,
            data_age_seconds=round(data_age, 1),
            selection_score=round(score, 4),
        )


def _regime_adjustment(trend: str | None, trend_strength: float | None) -> float:
    """Small, bounded tie-break that favours long-aligned markets.

    Spot automation is long-only, so an uptrend gets a bonus and a downtrend a
    penalty of at most ``REGIME_WEIGHT_PCT`` points. A missing or unclassified
    regime adds nothing.
    """
    if trend is None or trend_strength is None:
        return 0.0
    strength = min(max(trend_strength / 100.0, 0.0), 1.0)
    if trend == "uptrend":
        return REGIME_WEIGHT_PCT * strength
    if trend == "downtrend":
        return -REGIME_WEIGHT_PCT * strength
    return 0.0
