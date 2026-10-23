"""
Grid & Walk-Forward Strategy Parameter Optimizer.
Executes combinatorial parameter searches and tests out-of-sample data to prevent curve fitting.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.application.services.backtesting_engine import BacktestingEngine
from src.domain.optimization.entities import (
    OptimizationConfig,
    OptimizationResult,
    OptimizationTarget,
    ParameterRange,
    ParameterSet,
)
from src.domain.strategy.entities import BacktestConfig
from src.shared.logging.setup import get_logger
from src.shared.plugins.registry import PluginRegistry

if TYPE_CHECKING:
    from src.domain.market.entities import Candle

logger = get_logger(__name__)


class ParameterOptimizer:
    """
    Optimizes strategy hyperparameters using Walk-Forward out-of-sample cross validation.
    """

    def __init__(self, engine: BacktestingEngine | None = None) -> None:
        self.engine = engine or BacktestingEngine()

    def _generate_parameter_grid(self, ranges: list[ParameterRange]) -> list[dict[str, Any]]:
        param_names = []
        param_values = []

        for r in ranges:
            param_names.append(r.name)
            vals = []
            curr = r.min_value
            while curr <= r.max_value + 1e-9:
                vals.append(int(curr) if r.is_integer else round(curr, 4))
                curr += r.step
            param_values.append(vals)

        combinations = []
        for combo in itertools.product(*param_values):
            combinations.append(dict(zip(param_names, combo, strict=False)))

        return combinations

    def optimize(
        self,
        config: OptimizationConfig,
        candles: list[Candle],
    ) -> OptimizationResult:
        if len(candles) < 30:
            raise ValueError("Insufficient candles for Walk-Forward optimization (min 30 required)")

        strat_cls = PluginRegistry.get_strategy(config.strategy_name)
        if not strat_cls:
            raise ValueError(f"Strategy '{config.strategy_name}' not found.")

        # Default parameter ranges if none provided
        ranges = config.parameter_ranges
        if not ranges:
            if config.strategy_name == "rsi_mean_reversion":
                ranges = [
                    ParameterRange(
                        name="rsi_period", min_value=10, max_value=20, step=5, is_integer=True
                    ),
                    ParameterRange(
                        name="oversold", min_value=25, max_value=35, step=5, is_integer=False
                    ),
                    ParameterRange(
                        name="stop_loss_pct",
                        min_value=2.0,
                        max_value=4.0,
                        step=1.0,
                        is_integer=False,
                    ),
                ]
            elif config.strategy_name == "ema_crossover":
                ranges = [
                    ParameterRange(
                        name="fast_ema", min_value=5, max_value=15, step=5, is_integer=True
                    ),
                    ParameterRange(
                        name="slow_ema", min_value=20, max_value=30, step=5, is_integer=True
                    ),
                    ParameterRange(
                        name="stop_loss_pct",
                        min_value=1.5,
                        max_value=3.0,
                        step=0.5,
                        is_integer=False,
                    ),
                ]
            else:
                ranges = [
                    ParameterRange(
                        name="stop_loss_pct",
                        min_value=1.5,
                        max_value=4.0,
                        step=0.5,
                        is_integer=False,
                    ),
                    ParameterRange(
                        name="take_profit_pct",
                        min_value=3.0,
                        max_value=8.0,
                        step=1.0,
                        is_integer=False,
                    ),
                ]

        param_grid = self._generate_parameter_grid(ranges)
        logger.info(
            "optimization_started",
            strategy=config.strategy_name,
            combinations_count=len(param_grid),
        )

        # Walk-Forward dataset splitting
        split_idx = int(len(candles) * config.walk_forward_split)
        in_sample_candles = candles[:split_idx]
        out_of_sample_candles = candles[split_idx:]

        evaluated_sets: list[ParameterSet] = []

        for p in param_grid:
            try:
                # 1. Evaluate In-Sample (Training)
                in_strategy = strat_cls(parameters=p)
                in_cfg = BacktestConfig(
                    strategy_name=config.strategy_name,
                    symbol=config.symbol,
                    timeframe=config.timeframe,
                    parameters=p,
                )
                in_res = self.engine.run(in_strategy, in_sample_candles, in_cfg)

                # 2. Evaluate Out-of-Sample (Validation)
                out_strategy = strat_cls(parameters=p)
                out_cfg = BacktestConfig(
                    strategy_name=config.strategy_name,
                    symbol=config.symbol,
                    timeframe=config.timeframe,
                    parameters=p,
                )
                out_res = self.engine.run(out_strategy, out_of_sample_candles, out_cfg)

                # Score determination
                if config.target_metric == OptimizationTarget.SHARPE:
                    score = in_res.sharpe_ratio
                elif config.target_metric == OptimizationTarget.PROFIT_FACTOR:
                    score = in_res.profit_factor
                elif config.target_metric == OptimizationTarget.WIN_RATE:
                    score = in_res.win_rate_pct
                else:
                    score = in_res.total_return_pct

                # Check robustness: out-of-sample shouldn't experience complete degradation
                is_robust = True
                if in_res.total_return_pct > 0 and out_res.total_return_pct < -10.0:
                    is_robust = False

                evaluated_sets.append(
                    ParameterSet(
                        rank=0,
                        parameters=p,
                        score=round(score, 2),
                        in_sample_return_pct=in_res.total_return_pct,
                        out_of_sample_return_pct=out_res.total_return_pct,
                        sharpe_ratio=in_res.sharpe_ratio,
                        profit_factor=in_res.profit_factor,
                        max_drawdown_pct=in_res.max_drawdown_pct,
                        win_rate_pct=in_res.win_rate_pct,
                        total_trades=in_res.total_trades + out_res.total_trades,
                        is_robust=is_robust,
                    )
                )
            except Exception as exc:
                logger.warning("param_combo_failed", combo=p, error=str(exc))

        # Sort and rank top results
        evaluated_sets.sort(key=lambda s: s.score, reverse=True)
        top_sets = evaluated_sets[:8]

        for i, s in enumerate(top_sets):
            s.rank = i + 1

        best = top_sets[0] if top_sets else None
        best_params = best.parameters if best else {}
        best_score = best.score if best else 0.0

        is_overfit = best is not None and (
            not best.is_robust or best.out_of_sample_return_pct < -5.0
        )

        if best is None:
            rec_summary = (
                "No parameter combinations were evaluated, so no recommendation is available."
            )
        elif is_overfit:
            rec_summary = (
                f"Caution: Best parameters achieved {best_score} {config.target_metric.value} on training data, "
                f"but experienced performance degradation on out-of-sample validation data ({best.out_of_sample_return_pct}%). "
                "Consider using more conservative stop-loss or broader parameter step sizes."
            )
        else:
            rec_summary = (
                f"Optimal configuration identified: {best_params}. "
                f"Achieved {best.in_sample_return_pct}% In-Sample Return and {best.out_of_sample_return_pct}% Out-of-Sample validation return "
                f"with Sharpe Ratio of {best.sharpe_ratio}."
            )

        return OptimizationResult(
            id=uuid4(),
            strategy_name=config.strategy_name,
            symbol=config.symbol,
            timeframe=config.timeframe,
            target_metric=config.target_metric,
            combinations_evaluated=len(evaluated_sets),
            best_parameters=best_params,
            best_score=best_score,
            top_parameter_sets=top_sets,
            is_overfit_warning=is_overfit,
            recommendation_summary=rec_summary,
        )
