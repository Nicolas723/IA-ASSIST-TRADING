"""
Agent domain entities and interfaces.

Defines the contract that all AI agents must implement and the data structures
used for inter-agent communication.

Design decisions:
- AgentTask is the universal input — same structure for all agents
- AgentOutput has a mandatory reasoning field — no black-box decisions
- Confidence is a first-class concept — used for coordinator weighting
- Each output records the LLM prompt/response for full auditability
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.domain.market.entities import MarketContext
from src.shared.types.common import Confidence, Price, SymbolStr

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentType(StrEnum):
    COORDINATOR = "coordinator"
    TECHNICAL_ANALYST = "technical_analyst"
    PRICE_ACTION_ANALYST = "price_action_analyst"
    SENTIMENT_ANALYST = "sentiment_analyst"
    MACRO_ANALYST = "macro_analyst"
    FUNDAMENTAL_ANALYST = "fundamental_analyst"
    RISK_MANAGER = "risk_manager"
    STRATEGY_OPTIMIZER = "strategy_optimizer"
    SYSTEM_SUPERVISOR = "system_supervisor"
    PERFORMANCE_COACH = "performance_coach"


class SignalDirection(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    SELL = "sell"
    STRONG_SELL = "strong_sell"
    HOLD = "hold"  # Maintain current position
    NO_SIGNAL = "no_signal"  # Insufficient data


class RiskFlagSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentStatus(StrEnum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DEGRADED = "degraded"  # Running in fallback mode


# ---------------------------------------------------------------------------
# Supporting Value Objects
# ---------------------------------------------------------------------------


class RiskFlag(BaseModel):
    """A risk concern raised by an agent during analysis."""

    model_config = {"frozen": True}

    code: str  # Machine-readable code, e.g. "HIGH_VOLATILITY"
    description: str  # Human-readable explanation
    severity: RiskFlagSeverity
    suggested_action: str | None = None  # What to do about it


class PriceTarget(BaseModel):
    """Suggested price levels from an agent's analysis."""

    model_config = {"frozen": True}

    entry: Price | None = None
    stop_loss: Price | None = None
    take_profit_1: Price | None = None
    take_profit_2: Price | None = None
    risk_reward_ratio: Decimal | None = None


class KeyFactor(BaseModel):
    """A specific factor that influenced an agent's conclusion."""

    model_config = {"frozen": True}

    name: str  # e.g., "RSI Oversold", "Bullish Engulfing"
    impact: str  # "bullish" | "bearish" | "neutral"
    weight: float  # Importance 0.0-1.0
    description: str  # Detailed explanation


# ---------------------------------------------------------------------------
# Core Entities
# ---------------------------------------------------------------------------


class AgentTask(BaseModel):
    """
    The universal input structure for all agents.

    Every agent receives the same task structure, allowing the coordinator
    to dispatch uniformly and compare outputs consistently.
    """

    task_id: UUID = Field(default_factory=uuid4)
    agent_type: AgentType
    symbol: SymbolStr
    market_context: MarketContext

    # Portfolio context (what positions we currently have)
    portfolio_snapshot: dict[str, object] = Field(default_factory=dict)

    # Analysis parameters
    timeframes_to_analyze: list[str] = Field(default=["1h", "4h", "1d"])
    lookback_candles: int = Field(default=200, ge=10)

    # Historical research context, already formatted with its own guardrails.
    # It is untrusted data: it must never override signals, risk, or output schema.
    research_context: str = ""

    # Task metadata
    priority: int = Field(default=5, ge=1, le=10)  # 10 = highest priority
    timeout_seconds: float = Field(default=30.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    context_notes: str = ""  # Extra instructions from coordinator


class AgentOutput(BaseModel):
    """
    The universal output structure from all agents.

    Every agent produces this exact structure — enabling the coordinator
    to aggregate, compare, and weight outputs consistently.

    The reasoning and llm_response fields ensure full auditability.
    """

    task_id: UUID
    agent_type: AgentType
    symbol: SymbolStr

    # Core decision
    signal: SignalDirection
    confidence: Confidence = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Agent-reported heuristic score in [0, 1]. It is not a calibrated probability "
            "of a profitable trade or of the signal being correct."
        ),
    )

    # Explanation (MANDATORY — no black-box decisions)
    reasoning: str = Field(
        description="Detailed explanation of why this signal was generated. "
        "Must include the key indicators, patterns, or data points "
        "that drove the conclusion.",
        min_length=50,
    )
    key_factors: list[KeyFactor] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)

    # Price suggestions
    price_targets: PriceTarget | None = None

    # LLM Auditability (populated when LLM was used)
    llm_provider: str | None = None
    llm_model: str | None = None
    prompt_sent: str | None = None  # Exact prompt sent to LLM
    llm_response: str | None = None  # Raw LLM response

    # Performance
    processing_time_ms: float = 0.0
    status: AgentStatus = AgentStatus.DONE
    error_message: str | None = None  # If status == FAILED

    # Fallback flag
    is_fallback: bool = False  # True if deterministic rules were used instead of LLM

    completed_at: datetime = Field(default_factory=datetime.utcnow)


class CoordinatorDecision(BaseModel):
    """
    The final decision produced by the AgentCoordinator.

    Aggregates outputs from all specialist agents into a single,
    weighted decision with full traceability.
    """

    id: UUID = Field(default_factory=uuid4)
    symbol: SymbolStr

    # Aggregated decision
    final_signal: SignalDirection
    aggregate_confidence: Confidence = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Magnitude of the committee's weighted directional score in [0, 1]. "
            "It is not a calibrated probability of success."
        ),
    )

    # Weighted contributions from each agent
    agent_outputs: list[AgentOutput] = Field(default_factory=list)
    agent_weights: dict[str, float] = Field(default_factory=dict)  # agent_type → weight

    # Consolidated reasoning
    reasoning_summary: str = Field(
        description="Coordinator's synthesis of all agent analyses",
        min_length=50,
    )
    consensus_level: float = Field(
        ge=0.0, le=1.0, description="Degree of agreement between agents (1.0 = full consensus)"
    )
    dissenting_agents: list[str] = Field(
        default_factory=list, description="Agents whose signal differed from the majority"
    )

    # Price targets (consolidated)
    recommended_entry: Price | None = None
    recommended_stop: Price | None = None
    recommended_target: Price | None = None

    # Risk assessment
    all_risk_flags: list[RiskFlag] = Field(default_factory=list)
    risk_approved: bool = False  # Set by RiskManager agent

    # Action recommendation
    action_recommendation: str = ""  # Plain-language recommendation
    requires_human_review: bool = False  # Flag unusual situations for review

    created_at: datetime = Field(default_factory=datetime.utcnow)
    analysis_duration_ms: float = 0.0


class AgentPerformanceMetrics(BaseModel):
    """Historical accuracy tracking for each agent."""

    agent_type: AgentType
    total_signals: int = 0
    correct_signals: int = 0
    incorrect_signals: int = 0
    abstained_signals: int = 0  # NO_SIGNAL outputs
    average_confidence: float = 0.0
    average_latency_ms: float = 0.0
    current_weight: float = Field(default=1.0, ge=0.0, le=2.0)

    # Rolling window metrics
    last_7d_accuracy: float = 0.0
    last_30d_accuracy: float = 0.0

    @property
    def overall_accuracy(self) -> float:
        if self.total_signals == 0:
            return 0.0
        return self.correct_signals / self.total_signals
