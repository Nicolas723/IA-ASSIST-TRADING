"""
Agent Registry — Plugin System for AI Agents.

Allows discovering and instantiating agents dynamically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.shared.exceptions.domain import AppError
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.agents.base import BaseAgent
    from src.domain.agents.entities import AgentType

logger = get_logger(__name__)


class AgentRegistry:
    """Registry for discovering and managing agent plugins."""

    def __init__(self) -> None:
        self._agents: dict[AgentType, type[BaseAgent]] = {}

    def register(self, agent_class: type[BaseAgent]) -> None:
        """Register an agent class."""
        # Need to instantiate temporarily to get the type or enforce a class property.
        # Since agent_type is an abstract property on instance, let's instantiate.
        try:
            # We can't easily instantiate if it requires args.
            # For simplicity, assuming agents can be constructed without args.
            instance = agent_class()
            agent_type = instance.agent_type
            if agent_type in self._agents:
                logger.warning("agent_already_registered", agent_type=agent_type.value)
            self._agents[agent_type] = agent_class
            logger.info(
                "agent_registered", agent_type=agent_type.value, agent_class=agent_class.__name__
            )
        except Exception as e:
            logger.error(
                "agent_registration_failed", agent_class=agent_class.__name__, error=str(e)
            )

    def get_agent(self, agent_type: AgentType) -> BaseAgent:
        """Instantiate an agent by type."""
        agent_class = self._agents.get(agent_type)
        if not agent_class:
            raise AppError(f"Agent {agent_type.value} not registered")
        return agent_class()


# Singleton registry
agent_registry = AgentRegistry()
