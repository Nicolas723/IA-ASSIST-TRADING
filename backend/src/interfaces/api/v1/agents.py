"""AI Agents API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.application.services.agent_service import AgentInfo, AgentService
from src.domain.agents.entities import CoordinatorDecision
from src.interfaces.api.v1.deps import get_agent_service

router = APIRouter()


class AnalyzeRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    lookback_candles: int = Field(default=100, ge=20, le=500)


@router.post(
    "/analyze", response_model=CoordinatorDecision, summary="Run AI Multi-Agent Committee Analysis"
)
async def request_analysis(
    body: AnalyzeRequest,
    agent_service: AgentService = Depends(get_agent_service),
) -> CoordinatorDecision:
    """
    Triggers concurrent analysis across specialist AI agents (Technical Analyst,
    Price Action Specialist, Risk Controller) and synthesizes consensus decision via AgentCoordinator.
    """
    try:
        decision = await agent_service.run_analysis(
            symbol=body.symbol,
            timeframe=body.timeframe,
            lookback_candles=body.lookback_candles,
        )
        return decision
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"AI Agent Analysis failed: {exc}") from exc


@router.get("/list", response_model=list[AgentInfo], summary="List AI Specialist Agents")
async def list_agents(
    agent_service: AgentService = Depends(get_agent_service),
) -> list[AgentInfo]:
    """Return configured AI agents and their specializations."""
    return agent_service.list_agents()


@router.get("/status", summary="Get AI Agents System Status")
async def get_agents_status(
    agent_service: AgentService = Depends(get_agent_service),
) -> dict[str, object]:
    agents = agent_service.list_agents()
    return {
        "status": "ready",
        "total_agents": len(agents),
        "agents": agents,
    }
