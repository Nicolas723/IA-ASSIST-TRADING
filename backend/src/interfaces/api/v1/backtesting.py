"""Backtesting API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.application.services.market_data_service import MarketDataService
from src.application.services.strategy_service import StrategyService
from src.domain.market.entities import Timeframe
from src.domain.strategy.entities import BacktestConfig, BacktestResult
from src.interfaces.api.v1.deps import get_market_data_service, get_strategy_service

router = APIRouter()


@router.post("/run", response_model=BacktestResult, summary="Execute strategy backtest")
async def run_backtest(
    config: BacktestConfig,
    market_service: MarketDataService = Depends(get_market_data_service),
    strategy_service: StrategyService = Depends(get_strategy_service),
) -> BacktestResult:
    """
    Run an institutional backtest simulation:
    1. Fetches historical market candles (from DB cache or exchange)
    2. Runs event-driven order execution with slippage and fees
    3. Returns full performance metrics, trade logs, and equity curve
    """
    normalized_symbol = config.symbol.replace("-", "/").replace("_", "/").upper()
    try:
        tf = Timeframe(config.timeframe)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid timeframe '{config.timeframe}'"
        ) from exc

    candles = await market_service.get_candles(
        symbol=normalized_symbol,
        timeframe=tf,
        limit=config.limit_candles,
    )

    if not candles:
        raise HTTPException(
            status_code=404,
            detail=f"No market data available for {normalized_symbol} ({config.timeframe}). Please sync market data first.",
        )

    try:
        result = strategy_service.run_backtest(config=config, candles=candles)
        return result
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Backtesting error: {exc}") from exc


@router.get(
    "/{backtest_id}/results", response_model=BacktestResult, summary="Get cached backtest result"
)
async def get_results(
    backtest_id: str,
    strategy_service: StrategyService = Depends(get_strategy_service),
) -> BacktestResult:
    """Retrieve detailed results of a previous backtest execution."""
    result = strategy_service.get_backtest_result(backtest_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Backtest result '{backtest_id}' not found")
    return result
