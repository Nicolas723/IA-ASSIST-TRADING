"""
OptimizationService — Application Layer for Strategy Optimization and Performance Coaching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.market.entities import Timeframe
from src.domain.optimization.parameter_optimizer import ParameterOptimizer
from src.domain.optimization.performance_coach import PerformanceCoach
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.market_data_service import MarketDataService
    from src.domain.optimization.entities import (
        OptimizationConfig,
        OptimizationResult,
        PostTradeAnalysisReport,
    )
    from src.domain.strategy.entities import BacktestTrade

logger = get_logger(__name__)


class OptimizationService:
    """
    Coordinates hyperparameter optimization and post-trade performance critique.
    """

    def __init__(
        self,
        optimizer: ParameterOptimizer | None = None,
        market_service: MarketDataService | None = None,
    ) -> None:
        self.optimizer = optimizer or ParameterOptimizer()
        self.market_service = market_service
        self.coach = PerformanceCoach()
        self._results_cache: dict[str, OptimizationResult] = {}

    async def run_optimization(self, config: OptimizationConfig) -> OptimizationResult:
        if not self.market_service:
            raise ValueError("MarketDataService is required for optimization")

        normalized_symbol = config.symbol.replace("-", "/").replace("_", "/").upper()
        tf = Timeframe(config.timeframe)

        candles = await self.market_service.get_candles(
            symbol=normalized_symbol,
            timeframe=tf,
            limit=config.limit_candles,
        )

        if not candles:
            raise ValueError(
                f"No market data available for {normalized_symbol} to run optimization."
            )

        result = self.optimizer.optimize(config=config, candles=candles)
        self._results_cache[str(result.id)] = result

        logger.info(
            "optimization_completed",
            strategy=config.strategy_name,
            symbol=normalized_symbol,
            best_score=result.best_score,
            combos=result.combinations_evaluated,
        )
        return result

    def get_result(self, result_id: str) -> OptimizationResult | None:
        return self._results_cache.get(result_id)

    def analyze_trades(self, trades: list[BacktestTrade]) -> PostTradeAnalysisReport:
        return self.coach.analyze_trades(trades)
