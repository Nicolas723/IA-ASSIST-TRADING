"""
Domain Entities and Value Objects for Strategies and Backtesting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SignalSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Signal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    side: SignalSide = SignalSide.HOLD
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""
    indicators_snapshot: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class StrategyMetadata(BaseModel):
    name: str
    version: str = "1.0.0"
    author: str = "AI Trading Assistant"
    description: str = ""
    risk_level: str = "medium"
    supported_timeframes: list[str] = Field(
        default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d"]
    )
    default_parameters: dict[str, Any] = Field(default_factory=dict)


class BacktestConfig(BaseModel):
    strategy_name: str
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    initial_capital: float = 10000.0
    fee_rate: float = 0.001  # 0.1% fee
    slippage: float = 0.0005  # 0.05% slippage
    parameters: dict[str, Any] = Field(default_factory=dict)
    limit_candles: int = 500


class BacktestTrade(BaseModel):
    id: int
    symbol: str
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    fee_paid: float
    holding_candles: int
    exit_reason: str


class EquityPoint(BaseModel):
    timestamp: str
    time: int
    equity: float
    drawdown_pct: float


class BacktestResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    strategy_name: str
    symbol: str
    timeframe: str
    initial_capital: float
    final_capital: float
    net_profit: float
    total_return_pct: float
    buy_and_hold_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    average_trade_pnl: float
    average_win: float
    average_loss: float
    trades: list[BacktestTrade]
    equity_curve: list[EquityPoint]
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
