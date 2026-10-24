"""API tests for descriptive shadow-evaluation aggregation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.services.market_scan_service import (
    ShadowCandidateEvaluation,
    ShadowEvaluationResult,
)
from src.interfaces.api.v1.agents import shadow_evaluation_summary


class FakeShadowRepository:
    def __init__(self, evaluations: list[ShadowEvaluationResult]) -> None:
        self.evaluations = evaluations

    async def list_evaluations(self) -> list[ShadowEvaluationResult]:
        return self.evaluations


def _evaluation(delta: float | None, scan_scanned_at: datetime) -> ShadowEvaluationResult:
    return ShadowEvaluationResult(
        scan_id=uuid4(),
        scan_scanned_at=scan_scanned_at,
        timeframe="1m",
        horizon_candles=1,
        horizon_seconds=60,
        status="complete",
        evaluated_at=datetime.now(UTC),
        candidates=[
            ShadowCandidateEvaluation(
                symbol="BTC/USDT",
                rank=1,
                status="measured",
                forward_return_pct=1.0,
            ),
            ShadowCandidateEvaluation(
                symbol="ETH/USDT",
                rank=2,
                status="measured",
                forward_return_pct=0.5,
            ),
        ],
        top_rank_mean_return_pct=1.0,
        alternatives_mean_return_pct=0.5,
        top_minus_alternatives_pct=delta,
        equal_weight_random_baseline_pct=0.75 if delta is not None else None,
        top_minus_random_baseline_pct=0.25 if delta is not None else None,
        evidence_status="exploratory" if delta is not None else "insufficient",
    )


@pytest.mark.asyncio
async def test_shadow_summary_counts_only_valid_top_vs_alternative_comparisons() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    repository = FakeShadowRepository(
        [
            _evaluation(0.5, start),
            _evaluation(1.5, start + timedelta(seconds=30)),
            _evaluation(None, start + timedelta(seconds=60)),
        ]
    )

    result = await shadow_evaluation_summary(repository)  # type: ignore[arg-type]

    assert result["execution_enabled"] is False
    assert result["blocks_current_shadow_scan"] is False
    assert result["sample_threshold_applies_to"] == "aggregate_statistical_validation_only"
    horizon = result["horizons"][0]  # type: ignore[index]
    assert horizon["evaluated_scans"] == 3
    assert horizon["measured_candidates"] == 6
    assert horizon["scans_with_top_vs_alternatives"] == 2
    assert horizon["non_overlapping_comparisons"] == 1
    assert horizon["mean_top_minus_alternatives_pct"] == 0.5
    assert horizon["scans_with_random_baseline"] == 2
    assert horizon["non_overlapping_random_baselines"] == 1
    assert horizon["mean_top_minus_equal_weight_random_pct"] == 0.25
    assert horizon["evidence_status"] == "insufficient"
