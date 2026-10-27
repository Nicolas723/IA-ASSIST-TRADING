"""MLOps & continuous-learning API Router — v1"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.application.services.adaptive_weights import (
    AdaptiveWeightsReport,
    build_adaptive_weights_report,
    compute_adaptive_weights,
)
from src.application.services.drift_monitor_service import DriftMonitorService, DriftReport
from src.application.services.learning_service import LearningService
from src.domain.analytics.entities import DecisionOutcome, PerformanceSummary
from src.domain.trading.entities import ExecutionMode
from src.infrastructure.persistence.repositories.decision_repository import (
    SqlDecisionRepository,
)
from src.infrastructure.persistence.repositories.trading_repository import (
    SqlTradingRepository,
)
from src.interfaces.api.v1.deps import get_decision_repository, get_trading_repository
from src.shared.config.settings import get_settings
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get(
    "/performance",
    response_model=PerformanceSummary,
    summary="Decision attribution and strategy/agent performance",
)
async def get_performance(
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
    trading_repository: Annotated[SqlTradingRepository, Depends(get_trading_repository)],
    symbol: str | None = Query(default=None),
    mode: ExecutionMode | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> PerformanceSummary:
    """
    Join the AI decision audit trail with the durable order journal.

    The endpoint returns aggregates and per-decision outcomes only; raw prompts
    and LLM responses are never exposed here.
    """
    service = LearningService()
    try:
        decisions = await decision_repository.list_decisions(
            symbol=symbol,
            limit=limit,
            since=since,
        )
        orders = await trading_repository.list_orders_for_attribution(
            mode=mode,
            symbol=symbol,
            since=since,
            limit=min(limit * 20, 5000),
        )
        return service.build_summary(decisions, orders)
    except Exception as exc:  # pragma: no cover - defensive DB fallback
        logger.warning("mlops_performance_unavailable", error=str(exc))
        return PerformanceSummary(data_available=False)


@router.get(
    "/agent-weights",
    response_model=AdaptiveWeightsReport,
    summary="Preview or inspect adaptive committee weights",
)
async def get_agent_weights(
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
    trading_repository: Annotated[SqlTradingRepository, Depends(get_trading_repository)],
) -> AdaptiveWeightsReport:
    """Weights the committee would use given each specialist's measured accuracy.

    Read-only. They only reach live decisions when
    ``ADAPTIVE_AGENT_WEIGHTS_ENABLED=true``; otherwise this is a preview.
    """
    settings = get_settings()
    try:
        return await build_adaptive_weights_report(
            decision_repository=decision_repository,
            trading_repository=trading_repository,
            enabled=settings.adaptive_agent_weights_enabled,
            min_samples=settings.adaptive_weights_min_samples,
        )
    except Exception as exc:  # pragma: no cover - defensive DB fallback
        logger.warning("mlops_agent_weights_unavailable", error=str(exc))
        return compute_adaptive_weights(
            [],
            enabled=settings.adaptive_agent_weights_enabled,
            min_samples=settings.adaptive_weights_min_samples,
        )


@router.get(
    "/drift",
    response_model=DriftReport,
    summary="Read-only performance drift between recent and baseline outcome windows",
)
async def get_drift(
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
    trading_repository: Annotated[SqlTradingRepository, Depends(get_trading_repository)],
    symbol: str | None = Query(default=None),
    mode: ExecutionMode | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    recent_window: int = Query(default=20, ge=1, le=500),
    baseline_window: int = Query(default=60, ge=1, le=2000),
) -> DriftReport:
    """
    Compare recent closed outcomes against the preceding baseline window.

    The report is descriptive and read-only: it never suspends trading, resizes
    positions, or changes agent weights. When either window has too few closed
    outcomes the status is ``insufficient_data`` instead of a false regime call.
    """
    service = LearningService()
    monitor = DriftMonitorService(
        recent_window=recent_window,
        baseline_window=baseline_window,
    )
    try:
        decisions = await decision_repository.list_decisions(
            symbol=symbol,
            limit=limit,
            since=since,
        )
        orders = await trading_repository.list_orders_for_attribution(
            mode=mode,
            symbol=symbol,
            since=since,
            limit=min(limit * 20, 10000),
        )
        outcomes = service.build_outcomes(decisions, orders)
    except Exception as exc:  # pragma: no cover - defensive DB fallback
        logger.warning("mlops_drift_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail="Drift data unavailable") from exc

    return monitor.evaluate(outcomes, mode=mode, symbol=symbol)


@router.get(
    "/decisions/{decision_id}/outcome",
    response_model=DecisionOutcome,
    summary="Outcome attributable to one AI decision",
)
async def get_decision_outcome(
    decision_id: UUID,
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
    trading_repository: Annotated[SqlTradingRepository, Depends(get_trading_repository)],
) -> DecisionOutcome:
    """Return the entry/exit/PnL result linked to a single decision."""
    decision = await decision_repository.get_decision(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    orders = await trading_repository.list_orders_for_attribution(
        decision_id=decision_id,
        limit=500,
    )
    outcomes = LearningService().build_outcomes([decision], orders)
    return outcomes[0]
