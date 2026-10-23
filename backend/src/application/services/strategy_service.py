"""
StrategyService — Application Layer for Strategy Management and Backtesting Orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.application.services.backtesting_engine import BacktestingEngine
from src.shared.logging.setup import get_logger
from src.shared.plugins.registry import PluginRegistry

if TYPE_CHECKING:
    from src.domain.market.entities import Candle
    from src.domain.strategy.entities import BacktestConfig, BacktestResult, StrategyMetadata

logger = get_logger(__name__)


class StrategyService:
    """
    Coordinates strategy discovery, signal generation, and backtesting executions.
    """

    def __init__(self, engine: BacktestingEngine | None = None) -> None:
        self.engine = engine or BacktestingEngine()
        self._results_cache: dict[str, BacktestResult] = {}

    def list_strategies(self) -> list[StrategyMetadata]:
        """List all available strategies (built-in + plugins) with full metadata."""
        strategies: list[StrategyMetadata] = []
        for name in PluginRegistry.list_strategies():
            strat_cls = PluginRegistry.get_strategy(name)
            if strat_cls:
                strategies.append(strat_cls.metadata)
        return strategies

    def get_strategy_metadata(self, name: str) -> StrategyMetadata | None:
        strat_cls = PluginRegistry.get_strategy(name)
        if strat_cls:
            return strat_cls.metadata
        return None

    def run_backtest(
        self,
        config: BacktestConfig,
        candles: list[Candle],
    ) -> BacktestResult:
        """Run backtesting simulation for the requested strategy configuration."""
        strat_cls = PluginRegistry.get_strategy(config.strategy_name)
        if not strat_cls:
            raise ValueError(
                f"Strategy '{config.strategy_name}' not found. Available: {PluginRegistry.list_strategies()}"
            )

        strategy_instance = strat_cls(parameters=config.parameters)
        result = self.engine.run(strategy=strategy_instance, candles=candles, config=config)

        # Cache result
        self._results_cache[str(result.id)] = result
        logger.info(
            "backtest_completed",
            strategy=config.strategy_name,
            symbol=config.symbol,
            return_pct=result.total_return_pct,
            win_rate=result.win_rate_pct,
            trades_count=result.total_trades,
        )
        return result

    def get_backtest_result(self, backtest_id: str) -> BacktestResult | None:
        return self._results_cache.get(backtest_id)
