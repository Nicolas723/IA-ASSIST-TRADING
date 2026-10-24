"""AI Agents API Router — v1"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.application.services.agent_service import AgentInfo, AgentService
from src.application.services.market_data_service import MarketDataService
from src.application.services.market_scan_service import (
    MarketScanService,
    ShadowEvaluationResult,
    ShadowEvaluationService,
    ShadowScanResult,
)
from src.application.services.opportunity_service import (
    OpportunityAssessment,
    OpportunityAssessmentService,
)
from src.domain.agents.entities import CoordinatorDecision
from src.infrastructure.llm.gateway import LLMGateway
from src.infrastructure.persistence.repositories.shadow_scan_repository import (
    SqlShadowScanRepository,
)
from src.interfaces.api.v1.deps import (
    get_agent_service,
    get_llm_gateway,
    get_market_data_service,
    get_shadow_scan_repository,
)
from src.shared.config.settings import get_settings

router = APIRouter()


@router.get("/llm/status", summary="Show configured AI providers and fallback order")
async def get_llm_status(
    llm_gateway: LLMGateway | None = Depends(get_llm_gateway),
) -> dict[str, object]:
    """Expose provider availability without ever returning API credentials."""
    if llm_gateway is None:
        return {
            "mode": "deterministic_fallback",
            "primary_provider": None,
            "configured_providers": [],
            "fallback_providers": [],
        }
    return {
        "mode": "llm_with_fallback",
        "primary_provider": llm_gateway.primary_provider,
        "configured_providers": llm_gateway.configured_providers,
        "fallback_providers": llm_gateway.fallback_providers,
        "usage": llm_gateway.usage_status(),
    }


@router.post("/llm/health", summary="Check configured AI provider connectivity")
async def check_llm_providers(
    llm_gateway: LLMGateway | None = Depends(get_llm_gateway),
) -> dict[str, object]:
    """Check provider connectivity without sending market data or trading requests."""
    if llm_gateway is None:
        return {"mode": "deterministic_fallback", "providers": {}}
    return {
        "mode": "llm_with_fallback",
        "providers": await llm_gateway.check_providers_health(),
    }


class AnalyzeRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    lookback_candles: int = Field(default=100, ge=20, le=500)


class ShadowScanRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=5)
    timeframe: str = "1h"
    lookback_candles: int = Field(default=24, ge=2, le=100)


@router.post(
    "/analyze", response_model=CoordinatorDecision, summary="Run AI Multi-Agent Committee Analysis"
)
async def request_analysis(
    body: AnalyzeRequest,
    agent_service: AgentService = Depends(get_agent_service),
) -> CoordinatorDecision:
    """
    Triggers concurrent analysis across specialist AI agents (Technical Analyst,
    Price Action Specialist, Risk Controller) and synthesizes consensus decision via AgentCoordinator.
    """
    try:
        decision = await agent_service.run_analysis(
            symbol=body.symbol,
            timeframe=body.timeframe,
            lookback_candles=body.lookback_candles,
        )
        return decision
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"AI Agent Analysis failed: {exc}") from exc


@router.post(
    "/scan/shadow",
    response_model=ShadowScanResult,
    summary="Compare recent market movement without trading or LLM calls",
)
async def scan_markets_in_shadow(
    body: ShadowScanRequest,
    market_service: MarketDataService = Depends(get_market_data_service),
    repository: SqlShadowScanRepository = Depends(get_shadow_scan_repository),
) -> ShadowScanResult:
    """Read-only screening; the activity ranking is not an investment signal."""
    try:
        result = await MarketScanService(market_service).scan(
            symbols=body.symbols,
            timeframe=body.timeframe,
            lookback_candles=body.lookback_candles,
        )
        await repository.save(
            result,
            requested_symbols=[candidate.symbol for candidate in result.candidates],
            lookback_candles=body.lookback_candles,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Shadow scan could not be persisted; no trading order was sent.",
        ) from exc


@router.get(
    "/scan/shadow/history",
    response_model=list[ShadowScanResult],
    summary="List recent persisted shadow scans",
)
async def list_shadow_scan_history(
    repository: SqlShadowScanRepository = Depends(get_shadow_scan_repository),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ShadowScanResult]:
    """Read-only audit history of market snapshots; it does not imply profitability."""
    return await repository.list_recent(limit=limit)


@router.post(
    "/scan/shadow/{scan_id}/evaluate",
    response_model=ShadowEvaluationResult,
    summary="Measure forward price movement after a shadow scan",
)
async def evaluate_shadow_scan(
    scan_id: UUID,
    horizon_candles: int = Query(default=24, ge=1, le=168),
    market_service: MarketDataService = Depends(get_market_data_service),
    repository: SqlShadowScanRepository = Depends(get_shadow_scan_repository),
) -> ShadowEvaluationResult:
    """Evaluate only matured snapshots; this descriptive result is not a trade signal."""
    scan = await repository.get(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Shadow scan not found.")
    try:
        result = await ShadowEvaluationService(market_service).evaluate(scan, horizon_candles)
        await repository.save_evaluation(result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/scan/shadow/{scan_id}/evaluate-horizons",
    response_model=list[ShadowEvaluationResult],
    summary="Evaluate a shadow scan across several forward horizons",
)
async def evaluate_shadow_scan_horizons(
    scan_id: UUID,
    horizons_candles: list[int] = Query(default=[1, 6, 24], min_length=1, max_length=5),
    market_service: MarketDataService = Depends(get_market_data_service),
    repository: SqlShadowScanRepository = Depends(get_shadow_scan_repository),
) -> list[ShadowEvaluationResult]:
    """Evaluate horizons sequentially to protect the shared DB session and exchange limits."""
    if len(set(horizons_candles)) != len(horizons_candles):
        raise HTTPException(status_code=422, detail="horizons_candles must be unique.")
    scan = await repository.get(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Shadow scan not found.")
    evaluator = ShadowEvaluationService(market_service)
    results: list[ShadowEvaluationResult] = []
    try:
        for horizon in horizons_candles:
            result = await evaluator.evaluate(scan, horizon)
            await repository.save_evaluation(result)
            results.append(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return results


@router.post(
    "/scan/shadow/{scan_id}/assessment",
    response_model=OpportunityAssessment,
    summary="Apply read-only cost, spread, freshness and evidence gates",
)
async def assess_shadow_scan(
    scan_id: UUID,
    repository: Annotated[SqlShadowScanRepository, Depends(get_shadow_scan_repository)],
    now: Annotated[datetime | None, Query()] = None,
) -> OpportunityAssessment:
    """Abstain by default unless every gate passes. Never routes an order.

    The assessment is a policy record, not a prediction: passing gates only means
    a human may review the candidate, because the underlying evidence is
    descriptive forward price movement net of an estimated cost.
    """
    scan = await repository.get(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Shadow scan not found.")
    evaluations = await repository.list_evaluations()
    settings = get_settings()
    service = OpportunityAssessmentService(
        round_trip_cost_pct=float(settings.shadow_round_trip_cost_pct),
        min_net_edge_pct=float(settings.opportunity_min_net_edge_pct),
        max_quoted_spread_pct=float(settings.opportunity_max_quoted_spread_pct),
        max_scan_age_seconds=int(settings.opportunity_max_scan_age_seconds),
        min_validated_evaluations=int(settings.opportunity_min_validated_evaluations),
    )
    try:
        return service.assess(scan, evaluations, now=now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/scan/shadow/{scan_id}/evaluations",
    response_model=list[ShadowEvaluationResult],
    summary="Retrieve persisted evaluations for one shadow scan",
)
async def get_shadow_scan_evaluations(
    scan_id: UUID,
    repository: SqlShadowScanRepository = Depends(get_shadow_scan_repository),
) -> list[ShadowEvaluationResult]:
    """Return immutable evaluation snapshots without fetching market data again."""
    results = await repository.get_evaluations(scan_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Shadow scan not found.")
    return results


@router.get(
    "/scan/shadow/evaluations/summary", summary="Aggregate descriptive shadow evaluation evidence"
)
async def shadow_evaluation_summary(
    repository: SqlShadowScanRepository = Depends(get_shadow_scan_repository),
) -> dict[str, object]:
    """Summarize measured snapshots by horizon; no profitability inference is made."""
    results = await repository.list_evaluations()
    grouped: dict[tuple[str | None, int], list[ShadowEvaluationResult]] = {}
    for result in results:
        if result.status == "complete":
            grouped.setdefault((result.timeframe, result.horizon_candles), []).append(result)
    horizons: list[dict[str, object]] = []
    for (timeframe, horizon), evaluations in sorted(
        grouped.items(), key=lambda item: (item[0][0] or "", item[0][1])
    ):
        values = [
            item.top_minus_alternatives_pct
            for item in evaluations
            if item.top_minus_alternatives_pct is not None
        ]
        random_values = [
            item.top_minus_random_baseline_pct
            for item in evaluations
            if item.top_minus_random_baseline_pct is not None
        ]
        comparable = sorted(
            (
                item
                for item in evaluations
                if item.top_minus_alternatives_pct is not None and item.scan_scanned_at is not None
            ),
            key=lambda item: item.evaluation_anchor_at or item.scan_scanned_at or item.evaluated_at,
        )
        non_overlapping: list[ShadowEvaluationResult] = []
        last_sample_at = None
        for item in comparable:
            sample_at = item.evaluation_anchor_at or item.scan_scanned_at
            if sample_at is not None and (
                last_sample_at is None
                or sample_at >= last_sample_at + timedelta(seconds=item.horizon_seconds)
            ):
                non_overlapping.append(item)
                last_sample_at = sample_at
        non_overlapping_values = [
            item.top_minus_alternatives_pct
            for item in non_overlapping
            if item.top_minus_alternatives_pct is not None
        ]
        non_overlapping_random = [
            item.top_minus_random_baseline_pct
            for item in non_overlapping
            if item.top_minus_random_baseline_pct is not None
        ]
        non_overlapping_top_net = [
            item.top_rank_mean_net_return_pct
            for item in non_overlapping
            if item.top_rank_mean_net_return_pct is not None
        ]
        non_overlapping_random_net = [
            item.equal_weight_random_baseline_net_pct
            for item in non_overlapping
            if item.equal_weight_random_baseline_net_pct is not None
        ]
        count = len(values)
        measured_candidates = sum(
            sum(candidate.forward_return_pct is not None for candidate in item.candidates)
            for item in evaluations
        )
        horizons.append(
            {
                "horizon_candles": horizon,
                "timeframe": timeframe,
                "evaluated_scans": len(evaluations),
                "measured_candidates": measured_candidates,
                "scans_with_top_vs_alternatives": count,
                "non_overlapping_comparisons": len(non_overlapping_values),
                "mean_top_minus_alternatives_pct": (
                    round(sum(non_overlapping_values) / len(non_overlapping_values), 6)
                    if non_overlapping_values
                    else None
                ),
                "scans_with_random_baseline": len(random_values),
                "non_overlapping_random_baselines": len(non_overlapping_random),
                "mean_top_minus_equal_weight_random_pct": (
                    round(sum(non_overlapping_random) / len(non_overlapping_random), 6)
                    if non_overlapping_random
                    else None
                ),
                "mean_top_rank_net_return_pct": (
                    round(sum(non_overlapping_top_net) / len(non_overlapping_top_net), 6)
                    if non_overlapping_top_net
                    else None
                ),
                "mean_equal_weight_net_return_pct": (
                    round(sum(non_overlapping_random_net) / len(non_overlapping_random_net), 6)
                    if non_overlapping_random_net
                    else None
                ),
                "estimated_round_trip_cost_pcts": sorted(
                    {
                        item.estimated_round_trip_cost_pct
                        for item in evaluations
                        if item.estimated_round_trip_cost_pct is not None
                    }
                ),
                "evidence_status": (
                    "insufficient" if len(non_overlapping_values) < 30 else "exploratory"
                ),
                "interpretation": (
                    "Descriptive price movement net of an estimated round-trip cost; "
                    "not realised P&L or evidence of profitability."
                ),
            }
        )
    return {
        "execution_enabled": False,
        "blocks_current_shadow_scan": False,
        "sample_threshold_applies_to": "aggregate_statistical_validation_only",
        "horizons": horizons,
    }


@router.get("/list", response_model=list[AgentInfo], summary="List AI Specialist Agents")
async def list_agents(
    agent_service: AgentService = Depends(get_agent_service),
) -> list[AgentInfo]:
    """Return configured AI agents and their specializations."""
    return agent_service.list_agents()


@router.get("/status", summary="Get AI Agents System Status")
async def get_agents_status(
    agent_service: AgentService = Depends(get_agent_service),
) -> dict[str, object]:
    agents = agent_service.list_agents()
    return {
        "status": "ready",
        "total_agents": len(agents),
        "agents": agents,
    }
