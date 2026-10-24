"""Opportunity assessment gates abstain unless every cost and evidence gate passes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.services.market_scan_service import (
    ShadowCandidateEvaluation,
    ShadowEvaluationResult,
    ShadowScanCandidate,
    ShadowScanResult,
)
from src.application.services.opportunity_service import OpportunityAssessmentService

BASE = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def make_scan(
    *,
    scanned_at: datetime = BASE,
    spread: float | None = 0.05,
    movement: float | None = 0.80,
    alternative_movement: float | None = None,
) -> ShadowScanResult:
    alternative = (
        alternative_movement
        if alternative_movement is not None
        else (movement * 0.5 if movement is not None else None)
    )
    return ShadowScanResult(
        scan_id=uuid4(),
        timeframe="1h",
        scanned_at=scanned_at,
        candidates=[
            ShadowScanCandidate(
                symbol="BTC/USDT",
                status="observed",
                rank=1,
                last_price=60_000,
                latest_candle_at=scanned_at,
                lookback_return_pct=movement,
                activity_score=1.0,
                order_book_at=scanned_at,
                quoted_spread_pct=spread,
                observed_movement_less_spread_pct=movement,
                spread_status="observed" if spread is not None else "unavailable",
            ),
            ShadowScanCandidate(
                symbol="ETH/USDT",
                status="observed",
                rank=2,
                last_price=3_000,
                latest_candle_at=scanned_at,
                lookback_return_pct=alternative,
                activity_score=0.5,
                order_book_at=scanned_at,
                quoted_spread_pct=spread,
                observed_movement_less_spread_pct=alternative,
                spread_status="observed" if spread is not None else "unavailable",
            ),
        ],
    )


def make_evaluations(count: int, *, net_delta: float = 0.5) -> list[ShadowEvaluationResult]:
    evaluations: list[ShadowEvaluationResult] = []
    for index in range(count):
        anchor = BASE - timedelta(hours=index + 1)
        evaluations.append(
            ShadowEvaluationResult(
                scan_id=uuid4(),
                scan_scanned_at=anchor,
                evaluation_anchor_at=anchor,
                timeframe="1h",
                horizon_candles=1,
                horizon_seconds=3600,
                status="complete",
                evaluated_at=anchor + timedelta(hours=1),
                candidates=[
                    ShadowCandidateEvaluation(
                        symbol="BTC/USDT",
                        rank=1,
                        status="measured",
                        forward_return_pct=net_delta + 0.25,
                        estimated_cost_pct=0.25,
                        net_return_pct=net_delta,
                    )
                ],
                top_minus_alternatives_net_pct=net_delta,
                net_evidence_status="positive_after_costs",
                evidence_status="exploratory",
            )
        )
    return evaluations


def service(**overrides: float) -> OpportunityAssessmentService:
    options: dict[str, float] = {
        "round_trip_cost_pct": 0.25,
        "min_net_edge_pct": 0.10,
        "max_quoted_spread_pct": 0.10,
        "max_scan_age_seconds": 900,
        "min_validated_evaluations": 30,
    }
    options.update(overrides)
    return OpportunityAssessmentService(**options)  # type: ignore[arg-type]


def test_assessment_abstains_without_matured_evidence() -> None:
    assessment = service().assess(make_scan(), [], now=BASE + timedelta(minutes=1))

    assert assessment.decision == "abstain"
    assert assessment.selected_symbol is None
    assert assessment.execution_enabled is False
    assert assessment.automatic_routing is False
    assert "no matured net evidence" in assessment.selection_reason
    assert all(candidate.eligible for candidate in assessment.candidates)
    assert assessment.evidence["matured_evaluations"] == 0


def test_assessment_surfaces_top_candidate_only_when_every_gate_passes() -> None:
    assessment = service().assess(
        make_scan(),
        make_evaluations(30),
        now=BASE + timedelta(minutes=1),
    )

    assert assessment.decision == "eligible_for_manual_review"
    assert assessment.selected_symbol == "BTC/USDT"
    assert assessment.execution_enabled is False
    assert assessment.evidence["matured_evaluations"] == 30
    assert assessment.evidence["net_evidence_positive"] is True
    assert "Manual review only" in assessment.selection_reason


def test_assessment_abstains_when_scan_is_stale() -> None:
    assessment = service().assess(
        make_scan(),
        make_evaluations(30),
        now=BASE + timedelta(minutes=30),
    )

    assert assessment.decision == "abstain"
    assert any(
        "stale" in reason
        for candidate in assessment.candidates
        for reason in candidate.abstain_reasons
    )


def test_assessment_abstains_when_spread_is_unknown() -> None:
    assessment = service().assess(
        make_scan(spread=None),
        make_evaluations(30),
        now=BASE + timedelta(minutes=1),
    )

    assert assessment.decision == "abstain"
    assert any(
        "spread unavailable" in reason
        for candidate in assessment.candidates
        for reason in candidate.abstain_reasons
    )


def test_assessment_abstains_when_movement_does_not_clear_costs() -> None:
    assessment = service().assess(
        make_scan(movement=0.30),
        make_evaluations(30),
        now=BASE + timedelta(minutes=1),
    )

    assert assessment.decision == "abstain"
    assert all(not candidate.eligible for candidate in assessment.candidates)
    assert assessment.candidates[0].cost_adjusted_movement_pct == pytest.approx(0.05)


def test_assessment_abstains_when_net_evidence_is_negative() -> None:
    assessment = service().assess(
        make_scan(),
        make_evaluations(30, net_delta=-0.2),
        now=BASE + timedelta(minutes=1),
    )

    assert assessment.decision == "abstain"
    assert assessment.evidence["net_evidence_positive"] is False


def test_assessment_deduplicates_repeated_anchors() -> None:
    evaluations = make_evaluations(30)
    assessment = service().assess(
        make_scan(),
        evaluations + evaluations,
        now=BASE + timedelta(minutes=1),
    )

    assert assessment.evidence["evaluations_supplied"] == 60
    assert assessment.evidence["matured_evaluations"] == 30


def test_assessment_rejects_negative_cost() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        OpportunityAssessmentService(round_trip_cost_pct=-1)
