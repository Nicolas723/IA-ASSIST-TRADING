"""
Unit tests for AI Specialist Agents and Agent Coordinator.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.domain.agents.coordinator import AgentCoordinator
from src.domain.agents.entities import (
    AgentTask,
    AgentType,
)
from src.domain.agents.specialists.price_action_analyst import PriceActionAnalystAgent
from src.domain.agents.specialists.risk_manager import RiskManagerAgent
from src.domain.agents.specialists.technical_analyst import TechnicalAnalystAgent
from src.domain.market.entities import OHLCV, Candle, Exchange, MarketContext, Timeframe


def create_sample_task(agent_type: AgentType) -> AgentTask:
    now = datetime.now(tz=UTC)
    candle = Candle(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.H1,
        timestamp=now,
        ohlcv=OHLCV(
            open=Decimal("65000.0"),
            high=Decimal("66000.0"),
            low=Decimal("64500.0"),
            close=Decimal("65800.0"),
            volume=Decimal("150.0"),
        ),
    )
    context = MarketContext(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timestamp=now,
        candles={"1h": [candle]},
        indicators={"rsi_14": 28.5, "sma_20": 64000.0, "atr_14": 1200.0},
    )
    return AgentTask(
        agent_type=agent_type,
        symbol="BTC/USDT",
        market_context=context,
    )


@pytest.mark.asyncio
async def test_technical_analyst_agent():
    agent = TechnicalAnalystAgent(llm_gateway=None)
    task = create_sample_task(AgentType.TECHNICAL_ANALYST)

    output = await agent.run(task)

    assert output.agent_type == AgentType.TECHNICAL_ANALYST
    assert output.confidence > 0.0


@pytest.mark.asyncio
async def test_price_action_analyst_agent():
    agent = PriceActionAnalystAgent(llm_gateway=None)
    task = create_sample_task(AgentType.PRICE_ACTION_ANALYST)

    output = await agent.run(task)

    assert output.agent_type == AgentType.PRICE_ACTION_ANALYST
    assert output.confidence > 0.0


@pytest.mark.asyncio
async def test_risk_manager_agent():
    agent = RiskManagerAgent(llm_gateway=None)
    task = create_sample_task(AgentType.RISK_MANAGER)

    output = await agent.run(task)

    assert output.agent_type == AgentType.RISK_MANAGER
    assert output.confidence > 0.0
    assert isinstance(output.risk_flags, list)


@pytest.mark.asyncio
async def test_agent_coordinator_consensus():
    coordinator = AgentCoordinator(llm_gateway=None)
    task = create_sample_task(AgentType.COORDINATOR)

    decision = await coordinator.coordinate(task)

    assert decision.symbol == "BTC/USDT"
    assert len(decision.agent_outputs) == 3
    assert decision.consensus_level >= 0.0
    assert decision.aggregate_confidence > 0.0
    assert decision.action_recommendation != ""
