"""
Agent domain ports (interfaces).

Persistence contract for the AI decision audit trail.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from src.domain.agents.entities import CoordinatorDecision
    from src.domain.market.entities import SymbolStr


class DecisionRepositoryPort(abc.ABC):
    """
    Interface for persisting and querying the AI decision audit log.

    Implemented by the SQLAlchemy ``SqlDecisionRepository``.
    """

    @abc.abstractmethod
    async def save_decision(self, decision: CoordinatorDecision) -> None:
        """Persist a complete coordinator decision (idempotent by id)."""
        ...

    @abc.abstractmethod
    async def get_decision(self, decision_id: UUID) -> CoordinatorDecision | None:
        """Retrieve a single decision by id, or ``None`` if not found."""
        ...

    @abc.abstractmethod
    async def list_decisions(
        self,
        symbol: SymbolStr | None = None,
        limit: int = 20,
    ) -> list[CoordinatorDecision]:
        """List decisions, newest first, optionally filtered by symbol."""
        ...
