"""
AgentService — Application Layer for AI Agent Orchestration and Multi-Agent Committee Execution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from pydantic import BaseModel

from src.application.services.research_context import format_research_context
from src.domain.agents.coordinator import AgentCoordinator
from src.domain.agents.entities import (
    AgentTask,
    AgentType,
    CoordinatorDecision,
)
from src.domain.market.entities import Exchange, MarketContext, Timeframe
from src.shared.config.settings import get_settings
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.market_data_service import MarketDataService
    from src.domain.agents.ports import DecisionRepositoryPort
    from src.domain.market.entities import Candle
    from src.domain.research.entities import ResearchNote

logger = get_logger(__name__)


class ResearchContextPort(Protocol):
    """Read-only access to current research notes."""

    async def list_context(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 20,
    ) -> list[ResearchNote]: ...


def validate_market_data_freshness(
    candles: list[Candle], timeframe: Timeframe, now: datetime | None = None
) -> None:
    """Reject an analysis input when its newest candle is materially stale."""
    if not candles:
        raise ValueError("Cannot validate freshness without market candles.")

    latest_timestamp = max(candles, key=lambda candle: candle.timestamp).timestamp
    if latest_timestamp.tzinfo is None:
        latest_timestamp = latest_timestamp.replace(tzinfo=UTC)
    current_time = now or datetime.now(tz=UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)

    age_seconds = (current_time - latest_timestamp).total_seconds()
    maximum_age_seconds = timeframe.seconds * 2
    if age_seconds < -5:
        raise ValueError("Market data timestamp is in the future; refusing analysis.")
    if age_seconds > maximum_age_seconds:
        raise ValueError(
            f"Market data is stale: latest candle is {int(age_seconds)} seconds old "
            f"(maximum {maximum_age_seconds})."
        )


class AgentInfo(BaseModel):
    name: str
    agent_type: str
    role: str
    specialization: str


class AgentService:
    """
    Coordinates AI multi-agent committee execution and logs decision trails.
    """

    def __init__(
        self,
        coordinator: AgentCoordinator | None = None,
        market_service: MarketDataService | None = None,
        decision_repository: DecisionRepositoryPort | None = None,
        research_repository: ResearchContextPort | None = None,
    ) -> None:
        self.coordinator = coordinator or AgentCoordinator()
        self.market_service = market_service
        self.decision_repository = decision_repository
        self.research_repository = research_repository
        self._decision_history: list[CoordinatorDecision] = []

    def begin_cycle(self) -> None:
        """Reset the per-cycle LLM token counter before a new analysis run."""
        gateway = getattr(self.coordinator, "llm", None)
        if gateway is not None:
            gateway.begin_cycle()

    def list_agents(self) -> list[AgentInfo]:
        return [
            AgentInfo(
                name="Technical Confluence Specialist",
                agent_type=AgentType.TECHNICAL_ANALYST.value,
                role="Quantitative Indicators",
                specialization="Analyzes RSI, MACD, EMAs (9, 21, 50, 200), Bollinger Bands, ATR, SuperTrend.",
            ),
            AgentInfo(
                name="Price Action & SMC Specialist",
                agent_type=AgentType.PRICE_ACTION_ANALYST.value,
                role="Candlestick & Market Structure",
                specialization="Detects Pin bars, Engulfing patterns, Liquidity sweeps, Support & Resistance levels.",
            ),
            AgentInfo(
                name="Risk & Exposure Controller",
                agent_type=AgentType.RISK_MANAGER.value,
                role="Risk Containment",
                specialization="Evaluates ATR volatility, sets dynamic Stop-Loss & Take-Profit, approves risk parameters.",
            ),
            AgentInfo(
                name="Executive Decision Coordinator",
                agent_type=AgentType.COORDINATOR.value,
                role="Consensus & Synthesis",
                specialization="Weighs specialist votes, detects dissenting opinions, computes consensus confidence.",
            ),
        ]

    async def run_analysis(
        self,
        symbol: str,
        timeframe: str = "1h",
        lookback_candles: int = 100,
    ) -> CoordinatorDecision:
        normalized_symbol = symbol.replace("-", "/").replace("_", "/").upper()
        tf = Timeframe(timeframe)

        # Every analysis (scheduled cycle or manual /agents/analyze call) gets a
        # fresh per-cycle LLM budget. The daily caps are untouched.
        self.begin_cycle()

        if not self.market_service:
            raise ValueError("MarketDataService is required for AgentService")

        # 1. Fetch candles
        candles = await self.market_service.get_candles(
            symbol=normalized_symbol,
            timeframe=tf,
            limit=lookback_candles,
        )

        if not candles:
            raise ValueError(
                f"No market data available for {normalized_symbol} to run AI analysis."
            )

        try:
            validate_market_data_freshness(candles, tf)
        except ValueError as exc:
            raise ValueError(f"Market data for {normalized_symbol} is unusable: {exc}") from exc

        # 2. Build MarketContext
        context = MarketContext(
            symbol=normalized_symbol,
            exchange=Exchange.BINANCE,
            timestamp=datetime.now(tz=UTC),
            candles={timeframe: candles},
        )

        # 3. Create AgentTask
        task = AgentTask(
            task_id=uuid4(),
            agent_type=AgentType.COORDINATOR,
            symbol=normalized_symbol,
            market_context=context,
            timeframes_to_analyze=[timeframe],
            lookback_candles=max(len(candles), 10),
            research_context=await self._research_context_for(normalized_symbol, timeframe),
        )

        # 4. Run Coordinator
        decision = await self.coordinator.coordinate(task)

        # 5. Save to audit trail (in-memory cache + durable store)
        self._decision_history.insert(0, decision)
        if len(self._decision_history) > 100:
            self._decision_history = self._decision_history[:100]

        if self.decision_repository is not None:
            try:
                await self.decision_repository.save_decision(decision)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "decision_persistence_failed",
                    decision_id=str(decision.id),
                    error=str(exc),
                )

        logger.info(
            "ai_committee_analysis_completed",
            symbol=normalized_symbol,
            verdict=decision.final_signal.value,
            confidence=decision.aggregate_confidence,
            consensus=decision.consensus_level,
        )

        return decision

    async def _research_context_for(self, symbol: str, timeframe: str) -> str:
        """Load guarded research context only when explicitly enabled.

        A repository failure degrades to an empty context: research memory must
        never block or alter the deterministic trading path.
        """
        settings = get_settings()
        if not settings.research_context_in_prompts or self.research_repository is None:
            return ""
        try:
            notes = await self.research_repository.list_context(
                symbol=symbol,
                timeframe=timeframe,
                limit=settings.research_context_max_notes,
            )
        except Exception as exc:  # pragma: no cover - defensive DB fallback
            logger.warning("research_context_unavailable", symbol=symbol, error=str(exc))
            return ""
        context = format_research_context(
            notes,
            max_notes=settings.research_context_max_notes,
            max_chars=settings.research_context_max_chars,
        )
        if context:
            logger.info(
                "research_context_included_in_prompt",
                symbol=symbol,
                timeframe=timeframe,
                characters=len(context),
            )
        return context

    def get_audit_history(self, limit: int = 20) -> list[CoordinatorDecision]:
        return self._decision_history[:limit]
