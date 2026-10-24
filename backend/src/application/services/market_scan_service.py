"""Read-only shadow scan across a small, explicitly supplied market universe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.application.services.agent_service import validate_market_data_freshness
from src.application.services.regime_service import classify_regime
from src.domain.market.entities import Timeframe
from src.shared.config.settings import get_settings

if TYPE_CHECKING:
    from src.application.services.market_data_service import MarketDataService
    from src.domain.market.entities import Candle


def _as_utc(value: datetime) -> datetime:
    """Normalize exchange/DB timestamps before comparing with UTC wall time."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ShadowScanCandidate(BaseModel):
    """A read-only snapshot; movement and spread observations are not trade signals."""

    symbol: str
    status: str
    rank: int | None = None
    last_price: Decimal | None = None
    latest_candle_at: datetime | None = None
    lookback_return_pct: float | None = None
    activity_score: float | None = None
    order_book_at: datetime | None = None
    quoted_spread_pct: float | None = None
    observed_movement_less_spread_pct: float | None = None
    spread_status: str = "unavailable"
    # Descriptive regime label from the same closed candles. It is context for
    # the operator, never a signal, forecast, or probability.
    regime_status: str = "insufficient_data"
    trend_regime: str | None = None
    volatility_regime: str | None = None
    regime_disclaimer: str = (
        "Descriptive classification of past closed candles; not a forecast, "
        "probability or trading signal."
    )
    error: str | None = None


class ShadowScanResult(BaseModel):
    scan_id: UUID = Field(default_factory=uuid4)
    timeframe: str
    scanned_at: datetime
    candidates: list[ShadowScanCandidate]
    ranking_basis: str = (
        "Absolute price movement over the lookback window; not expected return, "
        "success probability, or a recommendation to trade."
    )
    execution_enabled: bool = False


class ShadowCandidateEvaluation(BaseModel):
    symbol: str
    rank: int | None
    status: str
    entry_price: Decimal | None = None
    future_price: Decimal | None = None
    future_candle_at: datetime | None = None
    forward_return_pct: float | None = None
    estimated_cost_pct: float | None = None
    net_return_pct: float | None = None


class ShadowEvaluationResult(BaseModel):
    scan_id: UUID
    scan_scanned_at: datetime | None = None
    evaluation_anchor_at: datetime | None = None
    timeframe: str | None = None
    horizon_candles: int
    horizon_seconds: int
    status: str
    evaluated_at: datetime
    candidates: list[ShadowCandidateEvaluation]
    top_rank_mean_return_pct: float | None = None
    alternatives_mean_return_pct: float | None = None
    top_minus_alternatives_pct: float | None = None
    equal_weight_random_baseline_pct: float | None = None
    top_minus_random_baseline_pct: float | None = None
    estimated_round_trip_cost_pct: float | None = None
    top_rank_mean_net_return_pct: float | None = None
    alternatives_mean_net_return_pct: float | None = None
    top_minus_alternatives_net_pct: float | None = None
    equal_weight_random_baseline_net_pct: float | None = None
    net_evidence_status: str = "insufficient"
    evidence_status: str
    execution_enabled: bool = False


class ShadowEvaluationService:
    """Describe forward price movement for matured snapshots; never creates signals."""

    def __init__(self, market_service: MarketDataService) -> None:
        self.market_service = market_service

    async def evaluate(
        self,
        scan: ShadowScanResult,
        horizon_candles: int,
        now: datetime | None = None,
        round_trip_cost_pct: float | None = None,
    ) -> ShadowEvaluationResult:
        if not 1 <= horizon_candles <= 168:
            raise ValueError("horizon_candles must be between 1 and 168.")
        cost_pct = (
            round_trip_cost_pct
            if round_trip_cost_pct is not None
            else float(get_settings().shadow_round_trip_cost_pct)
        )
        if cost_pct < 0:
            raise ValueError("round_trip_cost_pct cannot be negative.")
        timeframe = Timeframe(scan.timeframe)
        evaluated_at = _as_utc(now or datetime.now(UTC))
        entry_anchors = [
            _as_utc(candidate.latest_candle_at) + timedelta(seconds=timeframe.seconds)
            if candidate.latest_candle_at is not None
            else _as_utc(scan.scanned_at)
            for candidate in scan.candidates
            if candidate.status == "observed" and candidate.last_price is not None
        ]
        evaluation_anchor_at = max(entry_anchors, default=_as_utc(scan.scanned_at))
        target_at = evaluation_anchor_at + timedelta(seconds=timeframe.seconds * horizon_candles)
        if evaluated_at < target_at:
            return ShadowEvaluationResult(
                scan_id=scan.scan_id,
                scan_scanned_at=scan.scanned_at,
                evaluation_anchor_at=evaluation_anchor_at,
                timeframe=scan.timeframe,
                horizon_candles=horizon_candles,
                horizon_seconds=timeframe.seconds * horizon_candles,
                status="pending",
                evaluated_at=evaluated_at,
                candidates=[],
                estimated_round_trip_cost_pct=round(cost_pct, 6),
                evidence_status="not_matured",
            )

        outputs: list[ShadowCandidateEvaluation] = []
        for candidate in scan.candidates:
            if candidate.status != "observed" or candidate.last_price is None:
                outputs.append(
                    ShadowCandidateEvaluation(
                        symbol=candidate.symbol, rank=candidate.rank, status="unavailable"
                    )
                )
                continue
            candidate_anchor = (
                _as_utc(candidate.latest_candle_at) + timedelta(seconds=timeframe.seconds)
                if candidate.latest_candle_at is not None
                else _as_utc(scan.scanned_at)
            )
            candidate_target = candidate_anchor + timedelta(
                seconds=timeframe.seconds * horizon_candles
            )
            candles = await self.market_service.get_candles(
                symbol=candidate.symbol,
                timeframe=timeframe,
                limit=1000,
                since=candidate_target - timedelta(seconds=timeframe.seconds),
            )
            eligible = sorted(
                (
                    c
                    for c in candles
                    if _as_utc(c.timestamp) + timedelta(seconds=timeframe.seconds)
                    >= candidate_target
                    and _as_utc(c.timestamp) + timedelta(seconds=timeframe.seconds) <= evaluated_at
                ),
                key=lambda c: _as_utc(c.timestamp),
            )
            if not eligible:
                outputs.append(
                    ShadowCandidateEvaluation(
                        symbol=candidate.symbol,
                        rank=candidate.rank,
                        status="missing_future_data",
                        entry_price=candidate.last_price,
                    )
                )
                continue
            future = eligible[0]
            ret = float((future.close - candidate.last_price) / candidate.last_price * Decimal(100))
            outputs.append(
                ShadowCandidateEvaluation(
                    symbol=candidate.symbol,
                    rank=candidate.rank,
                    status="measured",
                    entry_price=candidate.last_price,
                    future_price=future.close,
                    future_candle_at=future.timestamp,
                    forward_return_pct=round(ret, 6),
                    estimated_cost_pct=round(cost_pct, 6),
                    net_return_pct=round(ret - cost_pct, 6),
                )
            )

        measured = [item for item in outputs if item.forward_return_pct is not None]
        tops = [
            item.forward_return_pct
            for item in measured
            if item.rank == 1 and item.forward_return_pct is not None
        ]
        alternatives = [
            item.forward_return_pct
            for item in measured
            if item.rank != 1 and item.forward_return_pct is not None
        ]
        top_mean = round(sum(tops) / len(tops), 6) if tops else None
        alternatives_mean = (
            round(sum(alternatives) / len(alternatives), 6) if alternatives else None
        )
        delta = (
            round(top_mean - alternatives_mean, 6)
            if top_mean is not None and alternatives_mean is not None
            else None
        )
        random_baseline = (
            round(
                sum(
                    item.forward_return_pct
                    for item in measured
                    if item.forward_return_pct is not None
                )
                / len(measured),
                6,
            )
            if len(measured) >= 2
            else None
        )
        random_delta = (
            round(top_mean - random_baseline, 6)
            if top_mean is not None and random_baseline is not None
            else None
        )
        net_tops = [
            item.net_return_pct
            for item in measured
            if item.rank == 1 and item.net_return_pct is not None
        ]
        net_alternatives = [
            item.net_return_pct
            for item in measured
            if item.rank != 1 and item.net_return_pct is not None
        ]
        top_net_mean = round(sum(net_tops) / len(net_tops), 6) if net_tops else None
        alternatives_net_mean = (
            round(sum(net_alternatives) / len(net_alternatives), 6) if net_alternatives else None
        )
        net_delta = (
            round(top_net_mean - alternatives_net_mean, 6)
            if top_net_mean is not None and alternatives_net_mean is not None
            else None
        )
        random_net_baseline = (
            round(random_baseline - cost_pct, 6) if random_baseline is not None else None
        )
        return ShadowEvaluationResult(
            scan_id=scan.scan_id,
            scan_scanned_at=scan.scanned_at,
            evaluation_anchor_at=evaluation_anchor_at,
            timeframe=scan.timeframe,
            horizon_candles=horizon_candles,
            horizon_seconds=timeframe.seconds * horizon_candles,
            status="complete",
            evaluated_at=evaluated_at,
            candidates=outputs,
            top_rank_mean_return_pct=top_mean,
            alternatives_mean_return_pct=alternatives_mean,
            top_minus_alternatives_pct=delta,
            equal_weight_random_baseline_pct=random_baseline,
            top_minus_random_baseline_pct=random_delta,
            estimated_round_trip_cost_pct=round(cost_pct, 6),
            top_rank_mean_net_return_pct=top_net_mean,
            alternatives_mean_net_return_pct=alternatives_net_mean,
            top_minus_alternatives_net_pct=net_delta,
            equal_weight_random_baseline_net_pct=random_net_baseline,
            net_evidence_status=(
                "insufficient"
                if net_delta is None
                else "positive_after_costs"
                if net_delta > 0
                else "not_positive_after_costs"
            ),
            evidence_status="exploratory" if delta is not None else "insufficient",
        )


class MarketScanService:
    """Collect bounded snapshots without invoking LLMs or execution."""

    def __init__(self, market_service: MarketDataService) -> None:
        self.market_service = market_service

    async def scan(
        self, symbols: list[str], timeframe: str = "1h", lookback_candles: int = 24
    ) -> ShadowScanResult:
        tf = Timeframe(timeframe)
        normalized_symbols = [
            symbol.strip().upper().replace("-", "/").replace("_", "/") for symbol in symbols
        ]
        if not normalized_symbols or len(normalized_symbols) > 5:
            raise ValueError("Shadow scan accepts between 1 and 5 symbols per request.")
        if len(set(normalized_symbols)) != len(normalized_symbols):
            raise ValueError("Shadow scan symbols must be unique.")
        if lookback_candles < 2 or lookback_candles > 100:
            raise ValueError("lookback_candles must be between 2 and 100.")

        candidates: list[ShadowScanCandidate] = []
        # MarketDataService shares the request's AsyncSession. Scan sequentially
        # to avoid concurrent operations on one SQLAlchemy session and to keep
        # exchange request bursts bounded.
        for symbol in normalized_symbols:
            try:
                candidate = await self._scan_one(symbol, tf, lookback_candles)
            except Exception:
                candidates.append(
                    ShadowScanCandidate(
                        symbol=symbol,
                        status="unavailable",
                        error="Market data could not be retrieved or validated.",
                    )
                )
            else:
                candidates.append(candidate)

        ranked = sorted(
            (candidate for candidate in candidates if candidate.activity_score is not None),
            key=lambda item: item.activity_score or 0.0,
            reverse=True,
        )
        for rank, candidate in enumerate(ranked, start=1):
            candidate.rank = rank

        return ShadowScanResult(
            timeframe=tf.value,
            scanned_at=datetime.now(tz=UTC),
            candidates=candidates,
        )

    async def _scan_one(
        self, symbol: str, timeframe: Timeframe, lookback_candles: int
    ) -> ShadowScanCandidate:
        # Fetch enough history to label the regime (EMA slow + ATR percentile)
        # while keeping the observed movement window at the requested length.
        candles: list[Candle] = await self.market_service.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=max(lookback_candles, 120),
        )
        if len(candles) < 2:
            return ShadowScanCandidate(
                symbol=symbol,
                status="insufficient_data",
                error="At least two candles are required to calculate observed movement.",
            )

        now = datetime.now(UTC)
        closed_candles = [
            candle
            for candle in candles
            if _as_utc(candle.timestamp) + timedelta(seconds=timeframe.seconds) <= now
        ]
        if len(closed_candles) < 2:
            return ShadowScanCandidate(
                symbol=symbol,
                status="insufficient_data",
                error="At least two fully closed candles are required for a shadow scan.",
            )
        validate_market_data_freshness(closed_candles, timeframe)
        ordered_candles = sorted(closed_candles, key=lambda candle: candle.timestamp)
        movement_window = ordered_candles[-lookback_candles:]
        first_close = movement_window[0].close
        latest = movement_window[-1]
        if first_close <= 0:
            return ShadowScanCandidate(
                symbol=symbol,
                status="invalid_data",
                error="The first close price must be positive.",
            )

        change_pct = float((latest.close - first_close) / first_close * Decimal("100"))
        regime = classify_regime(
            ordered_candles,
            symbol=symbol,
            timeframe=timeframe.value,
            now=now,
        )
        candidate = ShadowScanCandidate(
            symbol=symbol,
            status="observed",
            last_price=latest.close,
            latest_candle_at=_as_utc(latest.timestamp),
            lookback_return_pct=round(change_pct, 4),
            activity_score=round(abs(change_pct), 4),
            regime_status=regime.status,
            trend_regime=regime.trend,
            volatility_regime=regime.volatility,
        )
        try:
            order_book = await self.market_service.get_order_book(symbol, limit=5)
            bid = order_book.best_bid
            ask = order_book.best_ask
            mid = order_book.mid_price
            quote_at = order_book.timestamp
            if quote_at.tzinfo is None:
                quote_at = quote_at.replace(tzinfo=UTC)
            age_seconds = (datetime.now(UTC) - quote_at).total_seconds()
            if age_seconds < -5 or age_seconds > 60:
                candidate.spread_status = "stale"
            elif bid is None or ask is None or mid is None or bid <= 0 or ask < bid or mid <= 0:
                candidate.spread_status = "invalid"
            else:
                spread_pct = float((ask - bid) / mid * Decimal("100"))
                candidate.order_book_at = quote_at
                candidate.quoted_spread_pct = round(spread_pct, 6)
                candidate.observed_movement_less_spread_pct = round(abs(change_pct) - spread_pct, 6)
                candidate.spread_status = "observed"
        except Exception:
            # Keep the market-movement snapshot, but explicitly mark cost data missing.
            candidate.spread_status = "unavailable"
        return candidate
