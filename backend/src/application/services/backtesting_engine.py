"""
Institutional Quantitative Backtesting Engine.
Simulates order execution, slippage, exchange fees, and computes institutional performance metrics.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING
from uuid import uuid4

from src.domain.market.entities import Timeframe
from src.domain.strategy.entities import (
    BacktestConfig,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    SignalSide,
)
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.market.entities import Candle
    from src.domain.strategy.base import BaseStrategy

logger = get_logger(__name__)


class BacktestingEngine:
    """
    High-performance backtesting simulation engine.
    """

    def run(
        self,
        strategy: BaseStrategy,
        candles: list[Candle],
        config: BacktestConfig,
    ) -> BacktestResult:
        if len(candles) < 10:
            raise ValueError("Insufficient candles for backtesting (minimum 10 required)")

        capital = config.initial_capital
        initial_capital = config.initial_capital
        fee_rate = config.fee_rate
        slippage = config.slippage

        # Benchmark calculation (Buy and Hold)
        start_price = float(candles[0].close)
        end_price = float(candles[-1].close)
        buy_and_hold_return_pct = round(((end_price - start_price) / start_price) * 100.0, 2)

        trades: list[BacktestTrade] = []
        equity_curve: list[EquityPoint] = []

        peak_equity = capital
        max_drawdown_pct = 0.0

        # Position tracking state
        in_position = False
        entry_price = 0.0
        entry_time = ""
        entry_candle_idx = 0
        quantity = 0.0
        stop_loss = 0.0
        take_profit = 0.0

        daily_returns: list[float] = []
        prev_equity = capital

        for i in range(len(candles)):
            current_candle = candles[i]
            current_close = float(current_candle.close)
            current_high = float(current_candle.high)
            current_low = float(current_candle.low)
            time_str = current_candle.timestamp.isoformat()
            time_unix = int(current_candle.timestamp.timestamp())

            history = candles[: i + 1]

            # 1. Check open position for Stop-Loss / Take-Profit exit triggers
            if in_position:
                exit_price = 0.0
                exit_reason = ""

                if stop_loss > 0 and current_low <= stop_loss:
                    # Stop loss triggered
                    exit_price = stop_loss * (1.0 - slippage)
                    exit_reason = "Stop Loss Hit"
                elif take_profit > 0 and current_high >= take_profit:
                    # Take profit triggered
                    exit_price = take_profit * (1.0 - slippage)
                    exit_reason = "Take Profit Hit"
                else:
                    # Check strategy signal for voluntary exit
                    sig = strategy.on_candle(current_candle, history)
                    if sig.side == SignalSide.SELL:
                        exit_price = current_close * (1.0 - slippage)
                        exit_reason = sig.reason or "Strategy SELL Signal"

                if exit_price > 0:
                    gross_value = quantity * exit_price
                    fee = gross_value * fee_rate
                    net_value = gross_value - fee
                    pnl = net_value - (quantity * entry_price)
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0

                    capital = capital + (quantity * entry_price) + pnl

                    trades.append(
                        BacktestTrade(
                            id=len(trades) + 1,
                            symbol=config.symbol,
                            side="BUY",
                            entry_time=entry_time,
                            exit_time=time_str,
                            entry_price=round(entry_price, 2),
                            exit_price=round(exit_price, 2),
                            quantity=round(quantity, 6),
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct, 2),
                            fee_paid=round(fee, 2),
                            holding_candles=i - entry_candle_idx,
                            exit_reason=exit_reason,
                        )
                    )

                    in_position = False
                    quantity = 0.0
                    entry_price = 0.0
                    stop_loss = 0.0
                    take_profit = 0.0

            # 2. Check for entry if not in position
            elif not in_position and i < len(candles) - 1:
                sig = strategy.on_candle(current_candle, history)
                if sig.side == SignalSide.BUY:
                    sim_entry_price = current_close * (1.0 + slippage)
                    # Allocate 95% of available capital
                    allocated = capital * 0.95
                    fee = allocated * fee_rate
                    net_allocated = allocated - fee
                    quantity = net_allocated / sim_entry_price

                    capital -= allocated
                    entry_price = sim_entry_price
                    entry_time = time_str
                    entry_candle_idx = i
                    stop_loss = sig.stop_loss or 0.0
                    take_profit = sig.take_profit or 0.0
                    in_position = True

            # 3. Calculate mark-to-market equity
            current_equity = capital + (quantity * current_close if in_position else 0.0)
            if current_equity > peak_equity:
                peak_equity = current_equity

            dd = ((peak_equity - current_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

            equity_curve.append(
                EquityPoint(
                    timestamp=time_str,
                    time=time_unix,
                    equity=round(current_equity, 2),
                    drawdown_pct=round(dd, 2),
                )
            )

            # Record period returns for Sharpe/Sortino
            if prev_equity > 0:
                ret = (current_equity - prev_equity) / prev_equity
                daily_returns.append(ret)
            prev_equity = current_equity

        # Close any lingering open position at last candle close
        if in_position:
            last_price = float(candles[-1].close)
            gross_value = quantity * last_price
            fee = gross_value * fee_rate
            net_value = gross_value - fee
            pnl = net_value - (quantity * entry_price)
            pnl_pct = ((last_price - entry_price) / entry_price) * 100.0
            capital = capital + (quantity * entry_price) + pnl

            trades.append(
                BacktestTrade(
                    id=len(trades) + 1,
                    symbol=config.symbol,
                    side="BUY",
                    entry_time=entry_time,
                    exit_time=candles[-1].timestamp.isoformat(),
                    entry_price=round(entry_price, 2),
                    exit_price=round(last_price, 2),
                    quantity=round(quantity, 6),
                    pnl=round(pnl, 2),
                    pnl_pct=round(pnl_pct, 2),
                    fee_paid=round(fee, 2),
                    holding_candles=len(candles) - 1 - entry_candle_idx,
                    exit_reason="End of Backtest Window",
                )
            )

        # Performance Metrics calculation
        final_capital = round(capital, 2)
        net_profit = round(final_capital - initial_capital, 2)
        total_return_pct = round((net_profit / initial_capital) * 100.0, 2)

        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]
        total_trades = len(trades)

        win_rate_pct = (
            round((len(winning_trades) / total_trades) * 100.0, 2) if total_trades > 0 else 0.0
        )

        gross_gains = sum(t.pnl for t in winning_trades)
        gross_losses = abs(sum(t.pnl for t in losing_trades))

        if gross_losses > 0:
            profit_factor = round(gross_gains / gross_losses, 2)
        elif gross_gains > 0:
            profit_factor = 99.0
        else:
            profit_factor = 0.0

        avg_trade_pnl = round(net_profit / total_trades, 2) if total_trades > 0 else 0.0
        avg_win = round(gross_gains / len(winning_trades), 2) if winning_trades else 0.0
        avg_loss = round(gross_losses / len(losing_trades), 2) if losing_trades else 0.0

        # Annualize with the configured candle frequency. Treating every
        # observation as daily materially misstates risk for intraday backtests.
        if len(daily_returns) > 1:
            timeframe = Timeframe(config.timeframe)
            periods_per_year = 365.0 * 24.0 * 60.0 * 60.0 / timeframe.seconds
            mean_ret = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
            std_dev = math.sqrt(variance)

            annual_risk_free_rate = 0.02
            rf_period = annual_risk_free_rate / periods_per_year
            sharpe = (
                (mean_ret - rf_period) / std_dev * math.sqrt(periods_per_year)
                if std_dev > 0
                else 0.0
            )

            # Downside deviation for Sortino
            downside_returns = [r for r in daily_returns if r < 0]
            if downside_returns:
                downside_var = sum(r**2 for r in downside_returns) / len(downside_returns)
                downside_std = math.sqrt(downside_var)
                sortino = (
                    (mean_ret - rf_period) / downside_std * math.sqrt(periods_per_year)
                    if downside_std > 0
                    else 0.0
                )
            else:
                sortino = sharpe
        else:
            sharpe = 0.0
            sortino = 0.0

        return BacktestResult(
            id=uuid4(),
            strategy_name=config.strategy_name,
            symbol=config.symbol,
            timeframe=config.timeframe,
            initial_capital=initial_capital,
            final_capital=final_capital,
            net_profit=net_profit,
            total_return_pct=total_return_pct,
            buy_and_hold_return_pct=buy_and_hold_return_pct,
            total_trades=total_trades,
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate_pct=win_rate_pct,
            profit_factor=profit_factor,
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            average_trade_pnl=avg_trade_pnl,
            average_win=avg_win,
            average_loss=avg_loss,
            trades=trades,
            equity_curve=equity_curve,
        )
