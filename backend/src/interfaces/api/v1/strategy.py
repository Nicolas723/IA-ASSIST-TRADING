"""Strategy & Optimization API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.application.services.optimization_service import OptimizationService
from src.application.services.strategy_service import StrategyService
from src.domain.optimization.entities import (
    OptimizationConfig,
    OptimizationResult,
    PostTradeAnalysisReport,
)
from src.domain.strategy.entities import BacktestTrade, StrategyMetadata
from src.interfaces.api.v1.deps import get_optimization_service, get_strategy_service

router = APIRouter()


@router.get(
    "", response_model=list[StrategyMetadata], summary="List all available trading strategies"
)
async def list_strategies(
    service: StrategyService = Depends(get_strategy_service),
) -> list[StrategyMetadata]:
    """Return all registered quantitative and plugin trading strategies."""
    return service.list_strategies()


@router.post(
    "/optimize",
    response_model=OptimizationResult,
    summary="Run Walk-Forward Strategy Parameter Optimization",
)
async def optimize_strategy(
    config: OptimizationConfig,
    service: OptimizationService = Depends(get_optimization_service),
) -> OptimizationResult:
    """
    Execute combinatorial hyperparameter optimization with Walk-Forward cross-validation
    to prevent curve fitting and detect out-of-sample degradation.
    """
    try:
        result = await service.run_optimization(config)
        return result
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Optimization failed: {exc}") from exc


@router.get(
    "/optimization/{result_id}",
    response_model=OptimizationResult,
    summary="Get optimization result",
)
async def get_optimization_result(
    result_id: str,
    service: OptimizationService = Depends(get_optimization_service),
) -> OptimizationResult:
    result = service.get_result(result_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Optimization result '{result_id}' not found")
    return result


@router.post(
    "/coach-critique",
    response_model=PostTradeAnalysisReport,
    summary="Run AI Performance Coach on trade history",
)
async def get_coach_critique(
    trades: list[BacktestTrade],
    service: OptimizationService = Depends(get_optimization_service),
) -> PostTradeAnalysisReport:
    """Analyze executed trades to decompose wins vs losses and extract actionable trading lessons."""
    return service.analyze_trades(trades)


@router.get("/{name}", response_model=StrategyMetadata, summary="Get strategy details")
async def get_strategy(
    name: str,
    service: StrategyService = Depends(get_strategy_service),
) -> StrategyMetadata:
    """Return metadata and default parameters for a specific strategy."""
    meta = service.get_strategy_metadata(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    return meta
