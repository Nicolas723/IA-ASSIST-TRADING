"""
Analytics entities for the continuous-learning feedback loop.

These models are deliberately persistence-agnostic: the application layer
builds outcomes from the decision audit log plus the durable order journal, and
the API exposes the aggregates without leaking raw prompts or LLM responses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from src.domain.trading.entities import ExecutionMode, TradeOutcome


class DecisionOutcomeStatus(StrEnum):
    UNEXECUTED = "unexecuted"
    OPEN = "open"
    CLOSED = "closed"


class DecisionOutcome(BaseModel):
    """The realised (or still open) result attributable to one decision."""

    decision_id: UUID
    decision_created_at: datetime
    symbol: str
    signal: str
    confidence: float
    mode: ExecutionMode | None = None
    status: DecisionOutcomeStatus = DecisionOutcomeStatus.UNEXECUTED

    order_count: int = 0
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    quantity: Decimal = Decimal("0")
    gross_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    net_pnl_pct: float = 0.0
    outcome: TradeOutcome = TradeOutcome.OPEN
    decision_hit: bool | None = None

    opened_at: datetime | None = None
    closed_at: datetime | None = None

    # agent_type -> True/False/None (None means neutral or not scorable)
    agent_hits: dict[str, bool | None] = Field(default_factory=dict)
    note: str = ""


class AgentPerformance(BaseModel):
    """Directional accuracy of one specialist agent over resolved decisions."""

    agent_type: str
    decisions_scored: int = 0
    correct: int = 0
    incorrect: int = 0
    abstained: int = 0
    hit_rate_pct: float = 0.0
    avg_confidence: float = 0.0
    avg_processing_time_ms: float = 0.0


class SignalPerformance(BaseModel):
    """Outcome quality grouped by the committee's final signal."""

    signal: str
    decisions: int = 0
    resolved: int = 0
    correct: int = 0
    hit_rate_pct: float = 0.0
    net_pnl: Decimal = Decimal("0")


class SymbolPerformance(BaseModel):
    """Trade performance grouped by symbol."""

    symbol: str
    decisions: int = 0
    resolved: int = 0
    win_rate_pct: float = 0.0
    net_pnl: Decimal = Decimal("0")


class ConfidenceBucket(BaseModel):
    """Descriptive directional hit rate for a heuristic score range."""

    lower: float
    upper: float
    decisions: int = 0
    correct: int = 0
    hit_rate_pct: float = 0.0
    minimum_sample_size: int = 30
    sample_sufficient: bool = False


class PerformanceSummary(BaseModel):
    """Aggregated learning metrics exposed by the MLOps API."""

    total_decisions: int = 0
    executed: int = 0
    resolved: int = 0
    open: int = 0
    unexecuted: int = 0

    win_rate_pct: float = 0.0
    net_pnl: Decimal = Decimal("0")
    gross_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    profit_factor: float | None = None
    avg_pnl: Decimal = Decimal("0")
    avg_confidence: float = 0.0

    by_agent: list[AgentPerformance] = Field(default_factory=list)
    by_signal: list[SignalPerformance] = Field(default_factory=list)
    by_symbol: list[SymbolPerformance] = Field(default_factory=list)
    confidence_buckets: list[ConfidenceBucket] = Field(default_factory=list)

    unattributed_orders: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    data_available: bool = True
