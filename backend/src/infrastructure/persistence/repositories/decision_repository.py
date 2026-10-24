"""
SQLAlchemy implementation of the AI Decision (audit) Repository.

Stores the complete :class:`CoordinatorDecision` as JSON alongside a few
denormalised columns for fast querying, so the audit trail is durable and
recoverable after restarts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from src.domain.agents.entities import CoordinatorDecision
from src.domain.agents.ports import DecisionRepositoryPort
from src.infrastructure.persistence.models.audit import DecisionLogModel
from src.infrastructure.persistence.repositories.trading_repository import _ensure_utc

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.domain.market.entities import SymbolStr


def decision_to_model(decision: CoordinatorDecision) -> DecisionLogModel:
    """Flatten a decision into queryable columns + a lossless JSON payload."""
    return DecisionLogModel(
        id=decision.id,
        symbol=decision.symbol,
        final_signal=decision.final_signal.value,
        aggregate_confidence=decision.aggregate_confidence,
        consensus_level=decision.consensus_level,
        risk_approved=decision.risk_approved,
        requires_human_review=decision.requires_human_review,
        recommended_entry=decision.recommended_entry,
        recommended_stop=decision.recommended_stop,
        recommended_target=decision.recommended_target,
        reasoning_summary=decision.reasoning_summary,
        action_recommendation=decision.action_recommendation,
        agent_count=len(decision.agent_outputs),
        payload=decision.model_dump(mode="json"),
        created_at=_ensure_utc(decision.created_at),
        analysis_duration_ms=decision.analysis_duration_ms,
    )


def model_to_decision(model: DecisionLogModel) -> CoordinatorDecision:
    """Reconstruct the full domain entity from the stored JSON payload."""
    return CoordinatorDecision.model_validate(model.payload)


class SqlDecisionRepository(DecisionRepositoryPort):
    """SQLAlchemy-backed AI decision audit log."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_decision(self, decision: CoordinatorDecision) -> None:
        await self._session.merge(decision_to_model(decision))
        await self._session.commit()

    async def get_decision(self, decision_id: UUID) -> CoordinatorDecision | None:
        model = await self._session.get(DecisionLogModel, decision_id)
        return model_to_decision(model) if model else None

    async def list_decisions(
        self,
        symbol: SymbolStr | None = None,
        limit: int = 20,
        since: datetime | None = None,
    ) -> list[CoordinatorDecision]:
        stmt: Any = select(DecisionLogModel)
        if symbol is not None:
            stmt = stmt.where(DecisionLogModel.symbol == symbol)
        if since is not None:
            stmt = stmt.where(DecisionLogModel.created_at >= _ensure_utc(since))
        stmt = stmt.order_by(DecisionLogModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [model_to_decision(m) for m in result.scalars().all()]
