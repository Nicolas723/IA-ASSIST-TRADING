"""
Agent Coordinator — Multi-Agent Consensus & Synthesis Engine.
Dispatches tasks concurrently to specialist agents, synthesizes outputs, weights confidence, and builds unified decision.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from src.domain.agents.entities import (
    AgentOutput,
    AgentTask,
    AgentType,
    CoordinatorDecision,
    RiskFlag,
    SignalDirection,
)
from src.domain.agents.specialists.price_action_analyst import PriceActionAnalystAgent
from src.domain.agents.specialists.risk_manager import RiskManagerAgent
from src.domain.agents.specialists.technical_analyst import TechnicalAnalystAgent
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.agents.base import BaseAgent
    from src.infrastructure.llm.gateway import LLMGateway

logger = get_logger(__name__)

# Default committee vote weights. Adaptive weighting rescales these from
# measured accuracy; see application/services/adaptive_weights.py.
DEFAULT_AGENT_WEIGHTS: dict[str, float] = {
    AgentType.TECHNICAL_ANALYST.value: 0.40,
    AgentType.PRICE_ACTION_ANALYST.value: 0.35,
    AgentType.RISK_MANAGER.value: 0.25,
}


class AgentCoordinator:
    """
    Coordinates multi-agent deliberations and builds consensus trading decisions.
    """

    def __init__(
        self,
        llm_gateway: LLMGateway | None = None,
        specialists: list[BaseAgent] | None = None,
        agent_weights: dict[str, float] | None = None,
    ) -> None:
        self.llm = llm_gateway
        self.agent_weights = dict(agent_weights or DEFAULT_AGENT_WEIGHTS)
        self.specialists = specialists or [
            TechnicalAnalystAgent(llm_gateway=llm_gateway),
            PriceActionAnalystAgent(llm_gateway=llm_gateway),
            RiskManagerAgent(llm_gateway=llm_gateway),
        ]

    def set_llm_gateway(self, llm_gateway: LLMGateway | None) -> None:
        """Swap the provider gateway in place after a UI configuration change."""
        self.llm = llm_gateway
        for specialist in self.specialists:
            # Injected specialists may not use an LLM at all.
            if hasattr(specialist, "llm"):
                specialist.llm = llm_gateway

    def set_agent_weights(self, weights: dict[str, float]) -> None:
        """Replace the vote weights; missing agents keep their default."""
        self.agent_weights = {**DEFAULT_AGENT_WEIGHTS, **weights}

    async def coordinate(self, task: AgentTask) -> CoordinatorDecision:
        start_time = time.monotonic()

        # 1. Run all specialist agents concurrently
        logger.info(
            "coordinator_dispatching_agents", symbol=task.symbol, agent_count=len(self.specialists)
        )
        agent_tasks = [agent.run(task) for agent in self.specialists]
        outputs: list[AgentOutput] = await asyncio.gather(*agent_tasks)

        # 2. Weighted synthesis (defaults: technical 0.40, price action 0.35, risk 0.25)
        weight_map = dict(self.agent_weights)

        score = 0.0
        total_weight = 0.0
        all_risk_flags: list[RiskFlag] = []
        dissenting: list[str] = []

        direction_scores = {
            SignalDirection.STRONG_BUY: 2.0,
            SignalDirection.BUY: 1.0,
            SignalDirection.NEUTRAL: 0.0,
            SignalDirection.HOLD: 0.0,
            SignalDirection.NO_SIGNAL: 0.0,
            SignalDirection.SELL: -1.0,
            SignalDirection.STRONG_SELL: -2.0,
        }

        for out in outputs:
            all_risk_flags.extend(out.risk_flags)
            w = weight_map.get(out.agent_type.value, 0.33)
            val = direction_scores.get(out.signal, 0.0)
            score += val * out.confidence * w
            total_weight += w

        normalized_score = score / total_weight if total_weight > 0 else 0.0

        # Determine consensus signal
        if normalized_score >= 0.7:
            final_signal = SignalDirection.STRONG_BUY
        elif normalized_score >= 0.25:
            final_signal = SignalDirection.BUY
        elif normalized_score <= -0.7:
            final_signal = SignalDirection.STRONG_SELL
        elif normalized_score <= -0.25:
            final_signal = SignalDirection.SELL
        else:
            final_signal = SignalDirection.NEUTRAL

        # Check for dissenting agents
        for out in outputs:
            if (
                final_signal in (SignalDirection.BUY, SignalDirection.STRONG_BUY)
                and out.signal in (SignalDirection.SELL, SignalDirection.STRONG_SELL)
            ) or (
                final_signal in (SignalDirection.SELL, SignalDirection.STRONG_SELL)
                and out.signal in (SignalDirection.BUY, SignalDirection.STRONG_BUY)
            ):
                dissenting.append(out.agent_type.value)

        consensus_level = 1.0 - (len(dissenting) / len(outputs)) if outputs else 1.0
        aggregate_confidence = min(1.0, max(0.0, abs(normalized_score)))

        # Extract recommended price targets from agents
        rec_entry = None
        rec_stop = None
        rec_target = None

        for out in outputs:
            if out.price_targets:
                if not rec_entry and out.price_targets.entry:
                    rec_entry = out.price_targets.entry
                if not rec_stop and out.price_targets.stop_loss:
                    rec_stop = out.price_targets.stop_loss
                if not rec_target and out.price_targets.take_profit_1:
                    rec_target = out.price_targets.take_profit_1

        duration_ms = (time.monotonic() - start_time) * 1000

        # Synthesize plain-language reasoning
        reasons = [
            f"[{out.agent_type.value.replace('_', ' ').title()}]: {out.reasoning}"
            for out in outputs
        ]
        reasoning_summary = " | ".join(reasons)

        action_rec = (
            f"Executive Committee Verdict: {final_signal.value.replace('_', ' ').upper()} "
            f"(committee signal-strength score: {round(aggregate_confidence * 100)}%). "
            "This score is not a probability of success."
        )

        return CoordinatorDecision(
            id=uuid4(),
            symbol=task.symbol,
            final_signal=final_signal,
            aggregate_confidence=round(aggregate_confidence, 2),
            agent_outputs=outputs,
            agent_weights=weight_map,
            reasoning_summary=reasoning_summary,
            consensus_level=round(consensus_level, 2),
            dissenting_agents=dissenting,
            recommended_entry=rec_entry,
            recommended_stop=rec_stop,
            recommended_target=rec_target,
            all_risk_flags=all_risk_flags,
            risk_approved=len([f for f in all_risk_flags if f.severity == "critical"]) == 0,
            action_recommendation=action_rec,
            analysis_duration_ms=round(duration_ms, 2),
        )
