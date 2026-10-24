"""
Read-only opportunity assessment over a persisted shadow scan.

This module is a *policy* layer, not an execution layer. It converts a
descriptive shadow ranking plus any matured forward evaluations into an
explicit list of gates and abstention reasons. It never routes orders, never
calls an LLM, and never claims that observed movement predicts future return.

The default posture is abstention: a candidate is only surfaced for manual
review when data is fresh, the quoted spread and estimated round-trip cost are
known and acceptable, the observed movement still clears those costs by a
configured margin, and enough non-overlapping net evaluations exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.application.services.market_scan_service import (
        ShadowEvaluationResult,
        ShadowScanCandidate,
        ShadowScanResult,
    )

Decision = Literal["abstain", "eligible_for_manual_review"]


class CandidateAssessment(BaseModel):
    """Per-symbol gate results; every rejection carries an explicit reason."""

    symbol: str
    rank: int | None = None
    last_price: float | None = None
    lookback_return_pct: float | None = None
    quoted_spread_pct: float | None = None
    estimated_round_trip_cost_pct: float
    cost_adjusted_movement_pct: float | None = None
    gates: dict[str, bool] = Field(default_factory=dict)
    eligible: bool = False
    abstain_reasons: list[str] = Field(default_factory=list)


class OpportunityAssessment(BaseModel):
    """Auditable decision record for one shadow scan; execution stays disabled."""

    scan_id: str
    timeframe: str
    scanned_at: datetime
    generated_at: datetime
    decision: Decision
    selected_symbol: str | None = None
    selection_reason: str
    execution_enabled: bool = False
    automatic_routing: bool = False
    policy: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, object] = Field(default_factory=dict)
    candidates: list[CandidateAssessment] = Field(default_factory=list)


class OpportunityAssessmentService:
    """Apply explicit, conservative gates to a shadow scan. No side effects."""

    def __init__(
        self,
        *,
        round_trip_cost_pct: float = 0.25,
        min_net_edge_pct: float = 0.10,
        max_quoted_spread_pct: float = 0.10,
        max_scan_age_seconds: int = 900,
        min_validated_evaluations: int = 30,
    ) -> None:
        if round_trip_cost_pct < 0:
            raise ValueError("round_trip_cost_pct cannot be negative.")
        if min_net_edge_pct < 0:
            raise ValueError("min_net_edge_pct cannot be negative.")
        if max_quoted_spread_pct < 0:
            raise ValueError("max_quoted_spread_pct cannot be negative.")
        if max_scan_age_seconds < 0:
            raise ValueError("max_scan_age_seconds cannot be negative.")
        if min_validated_evaluations < 1:
            raise ValueError("min_validated_evaluations must be at least 1.")
        self.round_trip_cost_pct = round_trip_cost_pct
        self.min_net_edge_pct = min_net_edge_pct
        self.max_quoted_spread_pct = max_quoted_spread_pct
        self.max_scan_age_seconds = max_scan_age_seconds
        self.min_validated_evaluations = min_validated_evaluations

    def assess(
        self,
        scan: ShadowScanResult,
        evaluations: list[ShadowEvaluationResult] | None = None,
        now: datetime | None = None,
    ) -> OpportunityAssessment:
        reference = now or datetime.now(tz=UTC)
        scanned_at = _as_utc(scan.scanned_at)
        scan_age_seconds = max(0.0, (reference - scanned_at).total_seconds())
        scan_fresh = scan_age_seconds <= self.max_scan_age_seconds

        evidence = self._evidence_block(evaluations or [], reference)
        matured = cast("int", evidence["matured_evaluations"])
        evidence_positive = cast("bool", evidence["net_evidence_positive"])
        candidates = [
            self._assess_candidate(candidate, scan_fresh) for candidate in scan.candidates
        ]
        eligible = [candidate for candidate in candidates if candidate.eligible]

        reasons: list[str] = []
        if not scan_fresh:
            reasons.append(
                f"scan is {int(scan_age_seconds)}s old (limit {self.max_scan_age_seconds}s)."
            )
        if not evidence_positive:
            reasons.append(
                "no matured net evidence above costs; shadow results are descriptive only."
            )
        if matured < self.min_validated_evaluations:
            reasons.append(
                f"only {matured} matured evaluation(s); "
                f"{self.min_validated_evaluations} required for aggregate review."
            )
        if not eligible:
            reasons.append("no candidate passed the cost, spread, and freshness gates.")

        if eligible and not reasons:
            best = min(
                eligible,
                key=lambda item: item.rank if item.rank is not None else 10_000,
            )
            return self._result(
                scan,
                reference,
                candidates,
                evidence,
                decision="eligible_for_manual_review",
                selected_symbol=best.symbol,
                selection_reason=(
                    "Highest-ranked candidate passed every gate. Manual review only: "
                    "cost-adjusted historical movement is not a profitability forecast."
                ),
            )

        return self._result(
            scan,
            reference,
            candidates,
            evidence,
            decision="abstain",
            selected_symbol=None,
            selection_reason=" ".join(reasons) or "no validated edge available.",
        )

    def _result(
        self,
        scan: ShadowScanResult,
        reference: datetime,
        candidates: list[CandidateAssessment],
        evidence: dict[str, object],
        *,
        decision: Decision,
        selected_symbol: str | None,
        selection_reason: str,
    ) -> OpportunityAssessment:
        return OpportunityAssessment(
            scan_id=str(scan.scan_id),
            timeframe=scan.timeframe,
            scanned_at=_as_utc(scan.scanned_at),
            generated_at=reference,
            decision=decision,
            selected_symbol=selected_symbol,
            selection_reason=selection_reason,
            policy={
                "round_trip_cost_pct": self.round_trip_cost_pct,
                "min_net_edge_pct": self.min_net_edge_pct,
                "max_quoted_spread_pct": self.max_quoted_spread_pct,
                "max_scan_age_seconds": float(self.max_scan_age_seconds),
                "min_validated_evaluations": float(self.min_validated_evaluations),
            },
            evidence=evidence,
            candidates=candidates,
        )

    def _assess_candidate(
        self, candidate: ShadowScanCandidate, scan_fresh: bool
    ) -> CandidateAssessment:
        reasons: list[str] = []
        gates: dict[str, bool] = {}

        gates["scan_fresh"] = scan_fresh
        if not scan_fresh:
            reasons.append("scan snapshot is stale.")

        price = candidate.last_price
        has_price = price is not None and price > 0
        gates["price_available"] = has_price
        if not has_price:
            reasons.append("no usable last price.")

        spread = candidate.quoted_spread_pct
        spread_known = spread is not None and candidate.spread_status == "observed"
        gates["spread_known"] = spread_known
        if not spread_known:
            reasons.append("quoted spread unavailable; cost cannot be bounded.")

        spread_ok = spread is not None and spread <= self.max_quoted_spread_pct
        gates["spread_within_limit"] = spread_ok
        if spread is not None and not spread_ok:
            reasons.append(
                f"quoted spread {spread:.4f}% exceeds limit {self.max_quoted_spread_pct}%."
            )

        movement = candidate.observed_movement_less_spread_pct
        cost_adjusted: float | None = None
        if movement is None:
            gates["movement_available"] = False
            reasons.append("observed movement unavailable.")
        else:
            cost_adjusted = round(movement - self.round_trip_cost_pct, 6)
            gates["movement_available"] = True
            clears = cost_adjusted > self.min_net_edge_pct
            gates["clears_costs"] = clears
            if not clears:
                reasons.append(
                    f"movement net of spread and cost ({cost_adjusted:.4f}%) does not "
                    f"exceed {self.min_net_edge_pct}%."
                )

        eligible = all(gates.values())
        return CandidateAssessment(
            symbol=candidate.symbol,
            rank=candidate.rank,
            last_price=float(price) if price is not None else None,
            lookback_return_pct=candidate.lookback_return_pct,
            quoted_spread_pct=spread,
            estimated_round_trip_cost_pct=self.round_trip_cost_pct,
            cost_adjusted_movement_pct=cost_adjusted,
            gates=gates,
            eligible=eligible,
            abstain_reasons=reasons,
        )

    @staticmethod
    def _evidence_block(
        evaluations: list[ShadowEvaluationResult], reference: datetime
    ) -> dict[str, object]:
        # De-duplicate by anchor + horizon so repeated summary calls do not
        # inflate the apparent sample size.
        unique: dict[tuple[object, int], ShadowEvaluationResult] = {}
        for item in evaluations:
            if item.status != "complete" or item.top_minus_alternatives_net_pct is None:
                continue
            anchor = item.evaluation_anchor_at or item.scan_scanned_at or item.evaluated_at
            unique[(anchor, item.horizon_candles)] = item
        measured = list(unique.values())
        deltas = [
            item.top_minus_alternatives_net_pct
            for item in measured
            if item.top_minus_alternatives_net_pct is not None
        ]
        mean_net = round(sum(deltas) / len(deltas), 6) if deltas else None
        newest = max((item.evaluated_at for item in measured), default=None)
        return {
            "evaluations_supplied": len(evaluations),
            "matured_evaluations": len(measured),
            "mean_top_minus_alternatives_net_pct": mean_net,
            "net_evidence_positive": bool(mean_net is not None and mean_net > 0),
            "latest_evaluation_at": (_as_utc(newest).isoformat() if newest is not None else None),
            "reference_at": reference.isoformat(),
            "interpretation": (
                "Descriptive forward price movement net of estimated cost, deduplicated "
                "by anchor/horizon; may still contain overlapping windows. Not realised "
                "P&L, probability of profit, or trade authorisation."
            ),
        }


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
