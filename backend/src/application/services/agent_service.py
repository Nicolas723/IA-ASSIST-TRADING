"""
AgentService — Application Layer for AI Agent Orchestration and Multi-Agent Committee Execution.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
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
        decision_history: list[CoordinatorDecision] | None = None,
    ) -> None:
        self.coordinator = coordinator or AgentCoordinator()
        self.market_service = market_service
        self.decision_repository = decision_repository
        self.research_repository = research_repository
        # Background cycles build a short-lived AgentService per cycle. Passing
        # the shared list keeps the in-memory audit trail continuous for the UI.
        self._decision_history: list[CoordinatorDecision] = (
            decision_history if decision_history is not None else []
        )

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
        # Every analysis (scheduled cycle or manual /agents/analyze call) gets a
        # fresh per-cycle LLM budget. The daily caps are untouched.
        self.begin_cycle()

        normalized_symbol, _tf, candles, research_context = await self._prepare_analysis(
            symbol, timeframe, lookback_candles
        )
        decision = await self._coordinate_task(
            normalized_symbol, timeframe, candles, research_context
        )
        await self._record_decision(decision)
        return decision

    async def run_analyses(
        self,
        symbols: list[str],
        timeframe: str = "1h",
        lookback_candles: int = 100,
        parallel: bool = False,
    ) -> list[tuple[str, CoordinatorDecision]]:
        """Analyze several markets under one per-cycle LLM budget.

        Candles and research context are fetched sequentially because the market
        service shares a single AsyncSession; only the committee LLM calls run
        concurrently when ``parallel`` is true. A failure in one market never
        cancels the others.
        """
        normalized = list(
            dict.fromkeys(
                symbol.strip().replace("-", "/").replace("_", "/").upper()
                for symbol in symbols
                if symbol.strip()
            )
        )
        if not normalized:
            return []

        self.begin_cycle()
        prepared: list[tuple[str, Timeframe, list[Candle], str]] = []
        for symbol in normalized:
            try:
                prepared.append(await self._prepare_analysis(symbol, timeframe, lookback_candles))
            except Exception as exc:
                logger.warning(
                    "agent_analysis_input_failed",
                    symbol=symbol,
                    error=str(exc),
                )
        if not prepared:
            return []

        outcomes: list[Any] = []
        if parallel:
            outcomes = list(
                await asyncio.gather(
                    *(
                        self._coordinate_task(symbol, timeframe, candles, research)
                        for symbol, _tf, candles, research in prepared
                    ),
                    return_exceptions=True,
                )
            )
        else:
            for symbol, _tf, candles, research in prepared:
                try:
                    outcomes.append(
                        await self._coordinate_task(symbol, timeframe, candles, research)
                    )
                except Exception as exc:
                    outcomes.append(exc)

        results: list[tuple[str, CoordinatorDecision]] = []
        for (symbol, _tf, _candles, _research), outcome in zip(prepared, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning("agent_analysis_failed", symbol=symbol, error=str(outcome))
                continue
            await self._record_decision(outcome)
            results.append((symbol, outcome))
        return results

    async def _prepare_analysis(
        self,
        symbol: str,
        timeframe: str,
        lookback_candles: int,
    ) -> tuple[str, Timeframe, list[Candle], str]:
        """Fetch and validate everything the committee needs for one market."""
        normalized_symbol = symbol.strip().replace("-", "/").replace("_", "/").upper()
        tf = Timeframe(timeframe)

        if not self.market_service:
            raise ValueError("MarketDataService is required for AgentService")

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

        research_context = await self._research_context_for(normalized_symbol, timeframe)
        return normalized_symbol, tf, candles, research_context

    async def _coordinate_task(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Candle],
        research_context: str,
    ) -> CoordinatorDecision:
        context = MarketContext(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timestamp=datetime.now(tz=UTC),
            candles={timeframe: candles},
        )
        task = AgentTask(
            task_id=uuid4(),
            agent_type=AgentType.COORDINATOR,
            symbol=symbol,
            market_context=context,
            timeframes_to_analyze=[timeframe],
            lookback_candles=max(len(candles), 10),
            research_context=research_context,
        )
        return await self.coordinator.coordinate(task)

    async def _record_decision(self, decision: CoordinatorDecision) -> None:
        """Keep the in-memory audit trail and persist the durable copy."""
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
            symbol=decision.symbol,
            verdict=decision.final_signal.value,
            confidence=decision.aggregate_confidence,
            consensus=decision.consensus_level,
        )

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
