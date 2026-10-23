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


class AgentCoordinator:
    """
    Coordinates multi-agent deliberations and builds consensus trading decisions.
    """

    def __init__(
        self,
        llm_gateway: LLMGateway | None = None,
        specialists: list[BaseAgent] | None = None,
    ) -> None:
        self.llm = llm_gateway
        self.specialists = specialists or [
            TechnicalAnalystAgent(llm_gateway=llm_gateway),
            PriceActionAnalystAgent(llm_gateway=llm_gateway),
            RiskManagerAgent(llm_gateway=llm_gateway),
        ]

    async def coordinate(self, task: AgentTask) -> CoordinatorDecision:
        start_time = time.monotonic()

        # 1. Run all specialist agents concurrently
        logger.info(
            "coordinator_dispatching_agents", symbol=task.symbol, agent_count=len(self.specialists)
        )
        agent_tasks = [agent.run(task) for agent in self.specialists]
        outputs: list[AgentOutput] = await asyncio.gather(*agent_tasks)

        # 2. Weighted synthesis
        # Weights: Technical: 0.4, PriceAction: 0.35, Risk: 0.25
        weight_map = {
            AgentType.TECHNICAL_ANALYST.value: 0.40,
            AgentType.PRICE_ACTION_ANALYST.value: 0.35,
            AgentType.RISK_MANAGER.value: 0.25,
        }

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
        if aggregate_confidence < 0.4:
            aggregate_confidence = 0.65  # baseline confidence

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

        action_rec = f"Executive Committee Verdict: {final_signal.value.replace('_', ' ').upper()} with {round(aggregate_confidence * 100)}% conviction."

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
