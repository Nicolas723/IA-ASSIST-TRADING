"""
Performance Coach & Post-Trade Learning Engine.
Analyzes trade execution logs to extract actionable lessons and refine trading rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from src.domain.optimization.entities import PostTradeAnalysisReport, TradeLesson

if TYPE_CHECKING:
    from src.domain.strategy.entities import BacktestTrade


class PerformanceCoach:
    """
    Analyzes historical trade outcomes and generates psychological & quantitative feedback.
    """

    @staticmethod
    def analyze_trades(trades: list[BacktestTrade]) -> PostTradeAnalysisReport:
        if not trades:
            return PostTradeAnalysisReport(
                id=uuid4(),
                trades_analyzed_count=0,
                win_rate_pct=0.0,
                net_profit=0.0,
                lessons=[],
                ai_coach_critique="No completed trades available for post-trade retrospective analysis.",
            )

        total_trades = len(trades)
        winning_trades = [t for t in trades if t.pnl > 0]
        win_rate = round((len(winning_trades) / total_trades) * 100.0, 2)
        net_profit = round(sum(t.pnl for t in trades), 2)

        lessons: list[TradeLesson] = []

        # Analyze individual trades
        for t in trades[:10]:
            is_win = t.pnl > 0
            outcome = "WIN" if is_win else "LOSS"

            if not is_win and "Stop Loss" in t.exit_reason:
                category = "Risk Containment & SL Placement"
                insight = f"Trade #{t.id} stopped out with loss of -${abs(t.pnl):,.2f} ({t.pnl_pct:.2f}%). Stop Loss prevented further downside."
                adaptation = "Consider evaluating ATR volatility buffer to avoid false stop-outs during temporary market wicks."
            elif is_win and "Take Profit" in t.exit_reason:
                category = "Target Execution"
                insight = f"Trade #{t.id} successfully hit target with profit of +${t.pnl:,.2f} (+{t.pnl_pct:.2f}%)."
                adaptation = "Maintain discipline in taking scheduled profits at predefined resistance targets."
            elif is_win:
                category = "Trend Following Exit"
                insight = f"Trade #{t.id} exited with profit of +${t.pnl:,.2f} on strategy signal."
                adaptation = "Excellent trend capture. Trailing stop could potentially squeeze additional gains."
            else:
                category = "Exit Optimization"
                insight = f"Trade #{t.id} closed at -${abs(t.pnl):,.2f}."
                adaptation = (
                    "Review entry momentum filters to avoid low-liquidity consolidation chop."
                )

            lessons.append(
                TradeLesson(
                    trade_id=t.id,
                    symbol=t.symbol,
                    pnl=t.pnl,
                    outcome=outcome,
                    category=category,
                    insight=insight,
                    suggested_adaptation=adaptation,
                )
            )

        # Generate overarching AI Coach critique
        if win_rate >= 60.0 and net_profit > 0:
            critique = (
                f"Outstanding Performance: {win_rate}% win rate with ${net_profit:,.2f} net profit across {total_trades} trades. "
                "The strategy demonstrates solid edge and disciplined risk-to-reward execution. Keep risk sizing strictly capped."
            )
        elif net_profit > 0:
            critique = (
                f"Profitable Asymmetry: While win rate is moderate ({win_rate}%), the strategy remains net profitable (+${net_profit:,.2f}) "
                "due to positive risk/reward asymmetry (average win exceeds average loss). Continue letting winners run."
            )
        else:
            critique = (
                f"Optimization Needed: Strategy recorded a net loss of -${abs(net_profit):,.2f} ({win_rate}% win rate). "
                "Key observation: Tighten stop-losses or optimize parameter thresholds using the Strategy Parameter Optimizer."
            )

        return PostTradeAnalysisReport(
            id=uuid4(),
            trades_analyzed_count=total_trades,
            win_rate_pct=win_rate,
            net_profit=net_profit,
            lessons=lessons,
            ai_coach_critique=critique,
        )
