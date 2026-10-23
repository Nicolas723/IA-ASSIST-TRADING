"""
Abstract base class for all AI agents.

Every agent in the system implements this interface.
This enables:
1. Plugin discovery — any class implementing BaseAgent is loadable
2. Uniform dispatch by the coordinator
3. Consistent fallback behavior when LLM is unavailable
4. Testability — agents can be mocked with this interface
"""

from __future__ import annotations

import abc
import time
from typing import final

from src.domain.agents.entities import (
    AgentOutput,
    AgentStatus,
    AgentTask,
    AgentType,
    SignalDirection,
)
from src.shared.logging.setup import get_logger
from src.shared.observability.metrics import (
    agent_analyses_total,
    agent_analysis_duration_seconds,
    agent_confidence_score,
)

logger = get_logger(__name__)


class BaseAgent(abc.ABC):
    """
    Abstract base class for all specialist agents.

    Subclasses must implement:
    - agent_type: The AgentType enum value
    - analyze(): Core analysis logic

    The base class provides:
    - Timing instrumentation
    - Metrics recording
    - Fallback handling
    - Logging
    """

    @property
    @abc.abstractmethod
    def agent_type(self) -> AgentType:
        """Return the type identifier for this agent."""
        ...

    @abc.abstractmethod
    async def analyze(self, task: AgentTask) -> AgentOutput:
        """
        Perform analysis and return a structured output.

        Must always return an AgentOutput — never raise.
        On failure, return output with status=FAILED and appropriate defaults.

        Args:
            task: Structured input with market context and parameters

        Returns:
            AgentOutput with signal, confidence, and mandatory reasoning
        """
        ...

    @abc.abstractmethod
    async def fallback_analyze(self, task: AgentTask) -> AgentOutput:
        """
        Deterministic fallback analysis when LLM is unavailable.

        Uses rule-based logic to produce a basic signal.
        Must not make any external API calls.

        Args:
            task: Same task as analyze()

        Returns:
            AgentOutput with is_fallback=True
        """
        ...

    @final
    async def run(self, task: AgentTask) -> AgentOutput:
        """
        Execute the agent with instrumentation and error handling.

        This is the method called by the coordinator — not analyze() directly.
        Handles timing, metrics, logging, and fallback orchestration.
        """
        start_time = time.monotonic()
        agent_name = self.agent_type.value

        logger.info(
            "agent_started",
            agent=agent_name,
            task_id=str(task.task_id),
            symbol=task.symbol,
        )

        try:
            output = await self.analyze(task)
            duration_ms = (time.monotonic() - start_time) * 1000
            output = output.model_copy(update={"processing_time_ms": duration_ms})

            # Record metrics
            agent_analyses_total.labels(
                agent_type=agent_name,
                symbol=task.symbol,
                signal=output.signal.value,
            ).inc()
            agent_analysis_duration_seconds.labels(
                agent_type=agent_name,
                llm_provider=output.llm_provider or "none",
            ).observe(duration_ms / 1000)
            agent_confidence_score.labels(agent_type=agent_name).observe(output.confidence)

            logger.info(
                "agent_completed",
                agent=agent_name,
                task_id=str(task.task_id),
                signal=output.signal.value,
                confidence=output.confidence,
                duration_ms=round(duration_ms, 2),
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - start_time) * 1000
            logger.exception(
                "agent_failed",
                agent=agent_name,
                task_id=str(task.task_id),
                error=str(exc),
            )

            # Attempt deterministic fallback
            try:
                output = await self.fallback_analyze(task)
                output = output.model_copy(
                    update={
                        "status": AgentStatus.DEGRADED,
                        "processing_time_ms": duration_ms,
                        "is_fallback": True,
                        "error_message": str(exc),
                    }
                )
            except Exception as fallback_exc:
                logger.exception(
                    "agent_fallback_failed",
                    agent=agent_name,
                    task_id=str(task.task_id),
                    error=str(fallback_exc),
                )
                # Last resort: return a safe NO_SIGNAL output
                output = AgentOutput(
                    task_id=task.task_id,
                    agent_type=self.agent_type,
                    symbol=task.symbol,
                    signal=SignalDirection.NO_SIGNAL,
                    confidence=0.0,
                    reasoning=(
                        f"Agent {agent_name} failed completely. "
                        f"Primary error: {exc}. "
                        f"Fallback error: {fallback_exc}. "
                        "Returning NO_SIGNAL for safety."
                    ),
                    status=AgentStatus.FAILED,
                    processing_time_ms=duration_ms,
                    is_fallback=True,
                    error_message=str(exc),
                )

        return output

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} agent_type={self.agent_type.value}>"
