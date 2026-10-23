"""
Domain Entities and Value Objects for Strategy Parameter Optimization and Post-Trade Learning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class OptimizationTarget(StrEnum):
    SHARPE = "sharpe"
    PROFIT_FACTOR = "profit_factor"
    NET_RETURN = "net_return"
    WIN_RATE = "win_rate"


class ParameterRange(BaseModel):
    name: str
    min_value: float
    max_value: float
    step: float
    is_integer: bool = True


class ParameterSet(BaseModel):
    rank: int
    parameters: dict[str, Any]
    score: float
    in_sample_return_pct: float
    out_of_sample_return_pct: float
    sharpe_ratio: float
    profit_factor: float
    max_drawdown_pct: float
    win_rate_pct: float
    total_trades: int
    is_robust: bool = True


class OptimizationConfig(BaseModel):
    strategy_name: str = "rsi_mean_reversion"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    target_metric: OptimizationTarget = OptimizationTarget.SHARPE
    walk_forward_split: float = Field(
        default=0.7, ge=0.5, le=0.9, description="In-sample training split (e.g. 70%)"
    )
    parameter_ranges: list[ParameterRange] = Field(default_factory=list)
    limit_candles: int = 400


class OptimizationResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    strategy_name: str
    symbol: str
    timeframe: str
    target_metric: OptimizationTarget
    combinations_evaluated: int
    best_parameters: dict[str, Any]
    best_score: float
    top_parameter_sets: list[ParameterSet]
    is_overfit_warning: bool
    recommendation_summary: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class TradeLesson(BaseModel):
    trade_id: int
    symbol: str
    pnl: float
    outcome: str  # "WIN" | "LOSS"
    category: str
    insight: str
    suggested_adaptation: str


class PostTradeAnalysisReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    trades_analyzed_count: int
    win_rate_pct: float
    net_profit: float
    lessons: list[TradeLesson]
    ai_coach_critique: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
