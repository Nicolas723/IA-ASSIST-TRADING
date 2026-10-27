"""Adaptive committee weights derived from measured specialist accuracy.

This closes the learning loop: ``LearningService`` labels each closed decision
and scores every directional specialist on price direction. Here those hit
rates become vote weights for the next committee run.

Safeguards:
- Only directional specialists are adjusted; the risk manager keeps its weight.
- A specialist needs ``min_samples`` scored decisions before it moves at all.
- The hit rate is shrunk toward 50 % with a prior of ``prior_samples`` pseudo
  observations, so a lucky streak on few trades barely changes anything.
- The multiplier is bounded to [0.5, 1.5] and weights are renormalised to sum
  to 1, so no single agent can take over the committee.

The weights are a heuristic re-ranking of opinions, not a calibrated
probability, and they are only applied when ``ADAPTIVE_AGENT_WEIGHTS_ENABLED``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.application.services.learning_service import LearningService
from src.domain.agents.coordinator import DEFAULT_AGENT_WEIGHTS
from src.domain.agents.entities import AgentType

if TYPE_CHECKING:
    from src.domain.analytics.entities import AgentPerformance

_DIRECTIONAL_AGENTS = {
    AgentType.TECHNICAL_ANALYST.value,
    AgentType.PRICE_ACTION_ANALYST.value,
}
_MAX_SHIFT = 0.5


class AgentWeightDetail(BaseModel):
    agent_type: str
    default_weight: float
    weight: float
    multiplier: float
    decisions_scored: int
    hit_rate_pct: float | None
    adjusted: bool
    reason: str


class AdaptiveWeightsReport(BaseModel):
    enabled: bool
    applied: bool
    min_samples: int
    weights: dict[str, float]
    agents: list[AgentWeightDetail] = Field(default_factory=list)
    note: str = (
        "Los pesos reordenan la opinión de los analistas según su acierto direccional "
        "medido. No son una probabilidad de éxito."
    )


def compute_adaptive_weights(
    performance: list[AgentPerformance],
    *,
    enabled: bool,
    min_samples: int = 20,
    prior_samples: int = 20,
) -> AdaptiveWeightsReport:
    """Turn per-agent hit rates into bounded, normalised vote weights."""
    by_agent = {item.agent_type: item for item in performance}
    raw: dict[str, float] = {}
    details: list[AgentWeightDetail] = []

    for agent, default in DEFAULT_AGENT_WEIGHTS.items():
        stats = by_agent.get(agent)
        scored = (stats.correct + stats.incorrect) if stats else 0
        hit_rate = (stats.correct / scored * 100.0) if stats and scored else None
        multiplier = 1.0
        adjusted = False
        if agent not in _DIRECTIONAL_AGENTS:
            reason = "No direccional: conserva su peso."
        elif scored < min_samples:
            reason = f"Muestras insuficientes ({scored}/{min_samples})."
        else:
            assert stats is not None
            shrunk = (stats.correct + prior_samples * 0.5) / (scored + prior_samples)
            multiplier = 1.0 + (shrunk - 0.5) * 2.0 * _MAX_SHIFT
            multiplier = min(1.0 + _MAX_SHIFT, max(1.0 - _MAX_SHIFT, multiplier))
            adjusted = True
            reason = "Ajustado por acierto direccional."
        raw[agent] = default * multiplier
        details.append(
            AgentWeightDetail(
                agent_type=agent,
                default_weight=default,
                weight=0.0,
                multiplier=round(multiplier, 4),
                decisions_scored=scored,
                hit_rate_pct=round(hit_rate, 2) if hit_rate is not None else None,
                adjusted=adjusted,
                reason=reason,
            )
        )

    total = sum(raw.values()) or 1.0
    weights = {agent: round(value / total, 4) for agent, value in raw.items()}
    for detail in details:
        detail.weight = weights[detail.agent_type]

    applied = enabled and any(detail.adjusted for detail in details)
    return AdaptiveWeightsReport(
        enabled=enabled,
        applied=applied,
        min_samples=min_samples,
        weights=weights if applied else dict(DEFAULT_AGENT_WEIGHTS),
        agents=details,
    )


async def build_adaptive_weights_report(
    *,
    decision_repository: Any,
    trading_repository: Any,
    enabled: bool,
    min_samples: int,
    decision_limit: int = 500,
) -> AdaptiveWeightsReport:
    """Load the audit trail and journal, then compute the weights."""
    decisions = await decision_repository.list_decisions(limit=decision_limit)
    orders = await trading_repository.list_orders_for_attribution(
        limit=min(decision_limit * 20, 5000)
    )
    summary = LearningService().build_summary(decisions, orders)
    return compute_adaptive_weights(summary.by_agent, enabled=enabled, min_samples=min_samples)
