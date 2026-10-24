"""Unit tests for the read-only performance-drift monitor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.application.services.drift_monitor_service import DriftMonitorService
from src.domain.analytics.entities import DecisionOutcome, DecisionOutcomeStatus
from src.domain.trading.entities import ExecutionMode, TradeOutcome

_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _outcome(
    *,
    net_pct: float,
    closed_at: datetime,
    mode: ExecutionMode = ExecutionMode.PAPER,
    symbol: str = "BTC/USDT",
) -> DecisionOutcome:
    return DecisionOutcome(
        decision_id=uuid4(),
        decision_created_at=closed_at - timedelta(minutes=5),
        symbol=symbol,
        signal="buy",
        confidence=0.7,
        mode=mode,
        status=DecisionOutcomeStatus.CLOSED,
        net_pnl=Decimal(str(net_pct)),
        net_pnl_pct=net_pct,
        outcome=TradeOutcome.WIN if net_pct > 0 else TradeOutcome.LOSS,
        closed_at=closed_at,
    )


def _series(
    net_pcts: list[float],
    *,
    start: datetime = _NOW - timedelta(hours=30),
    mode: ExecutionMode = ExecutionMode.PAPER,
    symbol: str = "BTC/USDT",
) -> list[DecisionOutcome]:
    return [
        _outcome(
            net_pct=value,
            closed_at=start + timedelta(minutes=index),
            mode=mode,
            symbol=symbol,
        )
        for index, value in enumerate(net_pcts)
    ]


def _monitor() -> DriftMonitorService:
    return DriftMonitorService(recent_window=10, baseline_window=20)


def test_insufficient_data_when_windows_are_too_small() -> None:
    report = _monitor().evaluate(_series([1.0] * 5))

    assert report.status == "insufficient_data"
    assert report.recent.sample_size == 5
    assert report.baseline.sample_size == 0
    assert any("recent sample" in reason for reason in report.reasons)


def test_stable_when_recent_matches_baseline() -> None:
    report = _monitor().evaluate(_series([1.0] * 30))

    assert report.status == "stable"
    assert report.recent.sample_size == 10
    assert report.baseline.sample_size == 20
    assert report.recent.win_rate_pct == 100.0
    assert report.signals == [] or not any(signal.breached for signal in report.signals)


def test_watch_when_win_rate_drops_half_the_threshold() -> None:
    baseline = [1.0] * 20
    recent = [1.0] * 9 + [-1.0]

    report = _monitor().evaluate(_series(baseline + recent))

    assert report.status == "watch"
    win_signal = next(s for s in report.signals if s.metric == "win_rate_pct")
    assert win_signal.breached is False
    assert win_signal.delta == pytest.approx(-10.0)


def test_degraded_when_win_rate_breaches_threshold() -> None:
    baseline = [1.0] * 20
    recent = [1.0] * 5 + [-1.0] * 5

    report = _monitor().evaluate(_series(baseline + recent))

    assert report.status == "degraded"
    win_signal = next(s for s in report.signals if s.metric == "win_rate_pct")
    assert win_signal.breached is True
    assert win_signal.delta == pytest.approx(-50.0)


def test_degraded_when_average_return_falls_and_recent_window_loses() -> None:
    baseline = [2.0] * 20
    recent = [-1.0] * 10

    report = _monitor().evaluate(_series(baseline + recent))

    assert report.status == "degraded"
    return_signal = next(s for s in report.signals if s.metric == "avg_net_pnl_pct")
    assert return_signal.breached is True
    assert report.recent.total_net_pnl < 0


def test_filters_by_mode_and_symbol() -> None:
    paper = _series([1.0] * 30, mode=ExecutionMode.PAPER, symbol="BTC/USDT")
    live = _series(
        [-1.0] * 30,
        start=_NOW - timedelta(days=2),
        mode=ExecutionMode.REAL,
        symbol="ETH/USDT",
    )

    report = _monitor().evaluate(
        paper + live,
        mode=ExecutionMode.PAPER,
        symbol="BTC/USDT",
    )

    assert report.status == "stable"
    assert report.mode == "paper"
    assert report.symbol == "BTC/USDT"
    assert report.recent.sample_size == 10
    assert report.baseline.sample_size == 20


def test_missing_symbol_reports_insufficient_data() -> None:
    report = _monitor().evaluate(_series([1.0] * 30), symbol="SOL/USDT")

    assert report.status == "insufficient_data"
    assert report.recent.sample_size == 0


def test_report_is_read_only_and_disclaimed() -> None:
    report = _monitor().evaluate(_series([1.0] * 30))

    assert report.enforcement == "read_only_no_auto_suspension"
    assert "does not authorize" in report.disclaimer


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recent_window": 0},
        {"baseline_window": 0},
        {"min_recent": 0},
        {"min_baseline": 0},
        {"win_rate_drop_pct": 0.0},
        {"avg_return_drop_pct": -1.0},
    ],
)
def test_rejects_invalid_configuration(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        DriftMonitorService(**kwargs)
