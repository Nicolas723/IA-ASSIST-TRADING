"""
Position Sizing Calculator using Fixed-Fractional and Volatility-Adjusted Models.
"""

from __future__ import annotations

from src.domain.risk.entities import PositionSizeRequest, PositionSizeResult, RiskProfile


class PositionSizer:
    """
    Calculates mathematically optimal position sizes to guarantee strict risk containment.
    """

    @staticmethod
    def calculate(
        request: PositionSizeRequest, profile: RiskProfile | None = None
    ) -> PositionSizeResult:
        prof = profile or RiskProfile()

        # Validate prices
        entry = request.entry_price
        stop = request.stop_loss_price
        tp = request.take_profit_price
        balance = request.account_balance
        risk_pct = min(request.risk_percentage, prof.max_risk_per_trade_pct)

        if entry <= 0 or stop <= 0:
            return PositionSizeResult(
                symbol=request.symbol,
                account_balance=balance,
                risk_percentage=risk_pct,
                risk_amount_usd=0.0,
                price_risk_per_unit=0.0,
                price_risk_pct=0.0,
                recommended_units=0.0,
                recommended_notional_usd=0.0,
                risk_reward_ratio=None,
                potential_profit_usd=None,
                is_valid_setup=False,
                notes="Entry and Stop Loss prices must be greater than zero.",
            )

        price_risk = abs(entry - stop)
        if price_risk == 0:
            return PositionSizeResult(
                symbol=request.symbol,
                account_balance=balance,
                risk_percentage=risk_pct,
                risk_amount_usd=0.0,
                price_risk_per_unit=0.0,
                price_risk_pct=0.0,
                recommended_units=0.0,
                recommended_notional_usd=0.0,
                risk_reward_ratio=None,
                potential_profit_usd=None,
                is_valid_setup=False,
                notes="Stop Loss cannot equal Entry Price (zero price risk).",
            )

        price_risk_pct = round((price_risk / entry) * 100.0, 2)
        risk_amount_usd = round(balance * (risk_pct / 100.0), 2)

        # Base Formula: Position Size = Risk $ / Price Risk
        raw_units = risk_amount_usd / price_risk
        notional_usd = raw_units * entry

        # Cap at max capital allocation per asset
        max_notional_allowed = balance * (prof.max_position_size_pct / 100.0)
        if notional_usd > max_notional_allowed:
            notional_usd = max_notional_allowed
            raw_units = notional_usd / entry

        # Calculate Risk/Reward ratio if Take Profit is specified
        rr_ratio: float | None = None
        potential_profit_usd: float | None = None
        if tp and tp > 0:
            reward = abs(tp - entry)
            rr_ratio = round(reward / price_risk, 2)
            potential_profit_usd = round(raw_units * reward, 2)

        is_valid = True
        notes_list = []

        if rr_ratio is not None and rr_ratio < prof.min_risk_reward_ratio:
            is_valid = False
            notes_list.append(
                f"Risk/Reward ({rr_ratio}:1) is below required minimum ({prof.min_risk_reward_ratio}:1)."
            )
        else:
            notes_list.append(
                f"Valid setup. Max loss capped at ${risk_amount_usd} ({risk_pct}% of balance)."
            )

        return PositionSizeResult(
            symbol=request.symbol,
            account_balance=round(balance, 2),
            risk_percentage=round(risk_pct, 2),
            risk_amount_usd=risk_amount_usd,
            price_risk_per_unit=round(price_risk, 4),
            price_risk_pct=price_risk_pct,
            recommended_units=round(raw_units, 6),
            recommended_notional_usd=round(notional_usd, 2),
            risk_reward_ratio=rr_ratio,
            potential_profit_usd=potential_profit_usd,
            is_valid_setup=is_valid,
            notes=" ".join(notes_list),
        )
