"""Audit API Router — v1"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.application.services.agent_service import AgentService
from src.domain.agents.entities import CoordinatorDecision
from src.infrastructure.persistence.repositories.decision_repository import (
    SqlDecisionRepository,
)
from src.interfaces.api.v1.deps import get_agent_service, get_decision_repository
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get(
    "/decisions",
    response_model=list[CoordinatorDecision],
    summary="Get AI Decision Audit Trail",
)
async def get_decisions(
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
    agent_service: AgentService = Depends(get_agent_service),
    limit: int = Query(default=20, ge=1, le=100),
    symbol: str | None = Query(default=None),
) -> list[CoordinatorDecision]:
    """
    Retrieve the historical audit log of AI Multi-Agent decisions and reasoning trails.

    Reads from the durable ``decision_logs`` table (survives restarts). Falls back
    to the in-memory trail if the database is unavailable.
    """
    try:
        persisted = await decision_repository.list_decisions(symbol=symbol, limit=limit)
        if persisted:
            return persisted
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("audit_decisions_db_unavailable", error=str(exc))

    return agent_service.get_audit_history(limit=limit)


@router.get("/events", summary="Get system audit events")
async def get_events(limit: int = 50) -> dict[str, object]:  # noqa: ARG001 - reserved for pagination
    return {"events": [], "total": 0}
