"""
Read-only performance-drift monitor.

Compares a recent window of closed, attributed outcomes with the immediately
preceding baseline window. It reports whether results degraded and why, but it
never suspends trading or changes position sizing: enforcement stays explicit
and human-controlled. Small samples are reported as ``insufficient_data``
rather than as a regime change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from src.domain.analytics.entities import DecisionOutcome, DecisionOutcomeStatus
from src.domain.trading.entities import TradeOutcome

if TYPE_CHECKING:
    from src.domain.trading.entities import ExecutionMode

DriftStatus = Literal["insufficient_data", "stable", "watch", "degraded"]

_ZERO = Decimal("0")


class WindowMetrics(BaseModel):
    label: str
    sample_size: int
    win_rate_pct: float | None = None
    avg_net_pnl_pct: float | None = None
    total_net_pnl: Decimal = _ZERO
    profit_factor: float | None = None


class DriftSignal(BaseModel):
    metric: str
    recent: float | None = None
    baseline: float | None = None
    delta: float | None = None
    threshold: float
    breached: bool


class DriftReport(BaseModel):
    generated_at: datetime
    mode: str | None = None
    symbol: str | None = None
    status: DriftStatus
    reasons: list[str] = Field(default_factory=list)
    recent: WindowMetrics
    baseline: WindowMetrics
    signals: list[DriftSignal] = Field(default_factory=list)
    enforcement: str = "read_only_no_auto_suspension"
    disclaimer: str = (
        "Descriptive degradation check on closed outcomes. Small or "
        "non-stationary samples can look like drift; this is not a probability "
        "of future loss and does not authorize or block trades by itself."
    )


class DriftMonitorService:
    """Detect relative degradation between two adjacent outcome windows."""

    def __init__(
        self,
        *,
        recent_window: int = 20,
        baseline_window: int = 60,
        min_recent: int = 10,
        min_baseline: int = 20,
        win_rate_drop_pct: float = 15.0,
        avg_return_drop_pct: float = 0.5,
    ) -> None:
        if recent_window < 1 or baseline_window < 1:
            raise ValueError("windows must be positive.")
        if min_recent < 1 or min_baseline < 1:
            raise ValueError("minimum sample sizes must be positive.")
        if win_rate_drop_pct <= 0 or avg_return_drop_pct <= 0:
            raise ValueError("drop thresholds must be positive.")
        self.recent_window = recent_window
        self.baseline_window = baseline_window
        self.min_recent = min_recent
        self.min_baseline = min_baseline
        self.win_rate_drop_pct = win_rate_drop_pct
        self.avg_return_drop_pct = avg_return_drop_pct

    def evaluate(
        self,
        outcomes: list[DecisionOutcome],
        *,
        mode: ExecutionMode | None = None,
        symbol: str | None = None,
        now: datetime | None = None,
    ) -> DriftReport:
        reference = now or datetime.now(tz=UTC)
        closed = [
            outcome
            for outcome in outcomes
            if outcome.status == DecisionOutcomeStatus.CLOSED
            and (mode is None or outcome.mode == mode)
            and (symbol is None or outcome.symbol == symbol)
        ]
        closed.sort(key=_closed_at)
        recent_outcomes = closed[-self.recent_window :]
        baseline_outcomes = closed[
            -(self.recent_window + self.baseline_window) : -self.recent_window
        ]

        recent = _metrics("recent", recent_outcomes)
        baseline = _metrics("baseline", baseline_outcomes)

        if len(recent_outcomes) < self.min_recent or len(baseline_outcomes) < self.min_baseline:
            return DriftReport(
                generated_at=reference,
                mode=mode.value if mode else None,
                symbol=symbol,
                status="insufficient_data",
                reasons=[
                    f"recent sample {len(recent_outcomes)} < {self.min_recent}",
                    f"baseline sample {len(baseline_outcomes)} < {self.min_baseline}",
                ],
                recent=recent,
                baseline=baseline,
            )

        signals = self._signals(recent, baseline)
        breached = [signal for signal in signals if signal.breached]
        warning = [signal for signal in signals if _warning_breached(signal)]
        recent_losing = recent.total_net_pnl < _ZERO

        if any(signal.metric == "avg_net_pnl_pct" for signal in breached) and recent_losing:
            status: DriftStatus = "degraded"
        elif any(signal.metric == "win_rate_pct" for signal in breached):
            status = "degraded"
        elif warning:
            status = "watch"
        else:
            status = "stable"

        reasons = [
            f"{signal.metric} fell {abs(signal.delta or 0):.4f} against threshold "
            f"{signal.threshold:.4f}"
            for signal in breached or warning
        ]
        if status == "stable":
            reasons = ["recent window is within configured tolerance of the baseline."]

        return DriftReport(
            generated_at=reference,
            mode=mode.value if mode else None,
            symbol=symbol,
            status=status,
            reasons=reasons,
            recent=recent,
            baseline=baseline,
            signals=signals,
        )

    def _signals(self, recent: WindowMetrics, baseline: WindowMetrics) -> list[DriftSignal]:
        win_signal = _signal(
            "win_rate_pct",
            recent.win_rate_pct,
            baseline.win_rate_pct,
            self.win_rate_drop_pct,
        )
        return_signal = _signal(
            "avg_net_pnl_pct",
            recent.avg_net_pnl_pct,
            baseline.avg_net_pnl_pct,
            self.avg_return_drop_pct,
        )
        return [win_signal, return_signal]


def _closed_at(outcome: DecisionOutcome) -> datetime:
    value = outcome.closed_at or outcome.decision_created_at
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _metrics(label: str, outcomes: list[DecisionOutcome]) -> WindowMetrics:
    if not outcomes:
        return WindowMetrics(label=label, sample_size=0)
    wins = sum(1 for outcome in outcomes if outcome.outcome == TradeOutcome.WIN)
    returns = [outcome.net_pnl_pct for outcome in outcomes]
    gross_profit = sum((outcome.net_pnl for outcome in outcomes if outcome.net_pnl > 0), _ZERO)
    gross_loss = abs(sum((outcome.net_pnl for outcome in outcomes if outcome.net_pnl < 0), _ZERO))
    return WindowMetrics(
        label=label,
        sample_size=len(outcomes),
        win_rate_pct=round(wins / len(outcomes) * 100.0, 4),
        avg_net_pnl_pct=round(sum(returns) / len(returns), 6),
        total_net_pnl=sum((outcome.net_pnl for outcome in outcomes), _ZERO),
        profit_factor=(round(float(gross_profit / gross_loss), 4) if gross_loss > 0 else None),
    )


def _signal(
    metric: str,
    recent: float | None,
    baseline: float | None,
    threshold: float,
) -> DriftSignal:
    if recent is None or baseline is None:
        return DriftSignal(metric=metric, threshold=threshold, breached=False)
    delta = round(recent - baseline, 6)
    return DriftSignal(
        metric=metric,
        recent=recent,
        baseline=baseline,
        delta=delta,
        threshold=threshold,
        breached=delta <= -threshold,
    )


def _warning_breached(signal: DriftSignal) -> bool:
    """Half the configured drop is worth surfacing without declaring degradation."""
    return (
        not signal.breached and signal.delta is not None and signal.delta <= -(signal.threshold / 2)
    )
