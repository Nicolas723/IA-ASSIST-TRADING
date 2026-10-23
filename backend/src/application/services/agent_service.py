"""
AgentService — Application Layer for AI Agent Orchestration and Multi-Agent Committee Execution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel

from src.domain.agents.coordinator import AgentCoordinator
from src.domain.agents.entities import (
    AgentTask,
    AgentType,
    CoordinatorDecision,
)
from src.domain.market.entities import Exchange, MarketContext, Timeframe
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.market_data_service import MarketDataService
    from src.domain.agents.ports import DecisionRepositoryPort

logger = get_logger(__name__)


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
    ) -> None:
        self.coordinator = coordinator or AgentCoordinator()
        self.market_service = market_service
        self.decision_repository = decision_repository
        self._decision_history: list[CoordinatorDecision] = []

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

    def get_audit_history(self, limit: int = 20) -> list[CoordinatorDecision]:
        return self._decision_history[:limit]
