"""
Continuous-learning outcome builder.

The service joins the durable AI decision audit log with the durable trading
journal. It does not call an LLM and it never mutates agent weights: its job is
to produce trustworthy, explainable labels first.

Important conventions:
- A decision is ``closed`` only when an attributable entry and a full exit exist.
- A partial exit is reported as ``open`` with its realised PnL.
- Signal/agent accuracy is scored on price direction, not on net PnL, so fees
  do not turn a correct directional call into a wrong one.
- Risk-manager outputs are excluded from directional accuracy.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from src.domain.agents.entities import AgentType, CoordinatorDecision, SignalDirection
from src.domain.analytics.entities import (
    AgentPerformance,
    ConfidenceBucket,
    DecisionOutcome,
    DecisionOutcomeStatus,
    PerformanceSummary,
    SignalPerformance,
    SymbolPerformance,
)
from src.domain.trading.entities import Order, OrderSide, OrderStatus, TradeOutcome

if TYPE_CHECKING:
    from uuid import UUID

_ZERO = Decimal("0")
_TOLERANCE = Decimal("0.00000001")
# Exploratory reporting threshold only; this does not make the heuristic score
# statistically calibrated or sufficient to authorize a strategy.
_MIN_SCORE_BUCKET_SAMPLE = 30
_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.5),
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.0),
)


@dataclass
class _AgentStats:
    decisions_scored: int = 0
    correct: int = 0
    incorrect: int = 0
    abstained: int = 0
    confidence_sum: float = 0.0
    latency_sum: float = 0.0
    outputs: int = 0


@dataclass
class _SignalStats:
    decisions: int = 0
    resolved: int = 0
    correct: int = 0
    net_pnl: Decimal = _ZERO


@dataclass
class _SymbolStats:
    decisions: int = 0
    resolved: int = 0
    wins: int = 0
    net_pnl: Decimal = _ZERO


class LearningService:
    """Builds decision outcomes and aggregate learning metrics."""

    def build_outcomes(
        self,
        decisions: list[CoordinatorDecision],
        orders: list[Order],
    ) -> list[DecisionOutcome]:
        by_decision: dict[UUID, list[Order]] = defaultdict(list)
        seen_order_ids: set[UUID] = set()
        for order in orders:
            if order.id in seen_order_ids:
                continue
            seen_order_ids.add(order.id)
            if order.status != OrderStatus.FILLED:
                continue
            if order.coordinator_decision_id is None:
                continue
            by_decision[order.coordinator_decision_id].append(order)

        return [
            self._build_outcome(decision, by_decision.get(decision.id, []))
            for decision in decisions
        ]

    def build_summary(
        self,
        decisions: list[CoordinatorDecision],
        orders: list[Order],
    ) -> PerformanceSummary:
        outcomes = self.build_outcomes(decisions, orders)
        outcome_by_decision = {outcome.decision_id: outcome for outcome in outcomes}

        closed = [o for o in outcomes if o.status == DecisionOutcomeStatus.CLOSED]
        open_outcomes = [o for o in outcomes if o.status == DecisionOutcomeStatus.OPEN]
        wins = [o for o in closed if o.outcome == TradeOutcome.WIN]

        net_pnl = sum((o.net_pnl for o in outcomes), _ZERO)
        gross_pnl = sum((o.gross_pnl for o in outcomes), _ZERO)
        fees = sum((o.fees for o in outcomes), _ZERO)
        gross_profit = sum((o.net_pnl for o in closed if o.net_pnl > 0), _ZERO)
        gross_loss = abs(sum((o.net_pnl for o in closed if o.net_pnl < 0), _ZERO))
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else None

        avg_confidence = (
            sum(d.aggregate_confidence for d in decisions) / len(decisions) if decisions else 0.0
        )

        return PerformanceSummary(
            total_decisions=len(decisions),
            executed=sum(1 for o in outcomes if o.order_count > 0),
            resolved=len(closed),
            open=len(open_outcomes),
            unexecuted=sum(1 for o in outcomes if o.status == DecisionOutcomeStatus.UNEXECUTED),
            win_rate_pct=(len(wins) / len(closed) * 100.0) if closed else 0.0,
            net_pnl=net_pnl,
            gross_pnl=gross_pnl,
            fees=fees,
            profit_factor=profit_factor,
            avg_pnl=(net_pnl / len(closed)) if closed else _ZERO,
            avg_confidence=avg_confidence,
            by_agent=self._by_agent(decisions, outcome_by_decision),
            by_signal=self._by_signal(outcomes),
            by_symbol=self._by_symbol(outcomes),
            confidence_buckets=self._confidence_buckets(outcomes),
            unattributed_orders=sum(1 for order in orders if order.coordinator_decision_id is None),
            data_available=True,
        )

    # ------------------------------------------------------------------
    # Single-decision outcome
    # ------------------------------------------------------------------

    def _build_outcome(
        self,
        decision: CoordinatorDecision,
        linked_orders: list[Order],
    ) -> DecisionOutcome:
        ordered = sorted(
            linked_orders,
            key=lambda order: order.filled_at or order.created_at,
        )
        mode = ordered[0].mode if ordered else None
        agent_hits = self._agent_hits(decision, None)
        base = DecisionOutcome(
            decision_id=decision.id,
            decision_created_at=decision.created_at,
            symbol=decision.symbol,
            signal=decision.final_signal.value,
            confidence=decision.aggregate_confidence,
            mode=mode,
            order_count=len(ordered),
            agent_hits=agent_hits,
        )

        if not ordered:
            base.note = "No filled order linked to this decision."
            return base

        buys = [
            order
            for order in ordered
            if order.side == OrderSide.BUY
            and order.average_fill_price is not None
            and self._filled_qty(order) > 0
        ]
        sells = [
            order
            for order in ordered
            if order.side == OrderSide.SELL
            and order.average_fill_price is not None
            and self._filled_qty(order) > 0
        ]

        if not buys:
            base.note = "Exit-only execution: no attributable entry order."
            return base

        buy_qty = sum((self._filled_qty(order) for order in buys), _ZERO)
        sell_qty = sum((self._filled_qty(order) for order in sells), _ZERO)
        buy_notional = sum(
            (self._filled_qty(order) * (order.average_fill_price or _ZERO) for order in buys),
            _ZERO,
        )
        sell_notional = sum(
            (self._filled_qty(order) * (order.average_fill_price or _ZERO) for order in sells),
            _ZERO,
        )
        buy_fees = sum((self._fee_in_quote(order) for order in buys), _ZERO)
        sell_fees = sum((self._fee_in_quote(order) for order in sells), _ZERO)

        entry_price = buy_notional / buy_qty if buy_qty > 0 else None
        exit_price = sell_notional / sell_qty if sell_qty > 0 else None
        base.entry_price = entry_price
        base.exit_price = exit_price
        base.opened_at = buys[0].filled_at or buys[0].created_at

        if sell_qty <= 0:
            base.status = DecisionOutcomeStatus.OPEN
            base.quantity = buy_qty
            base.fees = buy_fees
            base.outcome = TradeOutcome.OPEN
            base.note = "Entry filled; position still open."
            return base

        matched_qty = min(buy_qty, sell_qty)
        matched_buy_fee = buy_fees * (matched_qty / buy_qty) if buy_qty > 0 else _ZERO
        matched_sell_fee = sell_fees * (matched_qty / sell_qty) if sell_qty > 0 else _ZERO
        gross = ((exit_price or _ZERO) - (entry_price or _ZERO)) * matched_qty
        fees = matched_buy_fee + matched_sell_fee
        net = gross - fees
        entry_notional = (entry_price or _ZERO) * matched_qty
        net_pct = float(net / entry_notional * 100) if entry_notional > 0 else 0.0

        base.quantity = matched_qty
        base.gross_pnl = gross
        base.fees = fees
        base.net_pnl = net
        base.net_pnl_pct = net_pct
        base.closed_at = sells[-1].filled_at or sells[-1].created_at

        fully_closed = sell_qty + _TOLERANCE >= buy_qty
        if fully_closed:
            base.status = DecisionOutcomeStatus.CLOSED
            base.outcome = (
                TradeOutcome.WIN
                if net > 0
                else TradeOutcome.LOSS
                if net < 0
                else TradeOutcome.BREAKEVEN
            )
            if sell_qty > buy_qty + _TOLERANCE:
                base.note = "Sell quantity exceeded entry quantity; PnL capped to entry."
        else:
            base.status = DecisionOutcomeStatus.OPEN
            base.outcome = TradeOutcome.OPEN
            base.note = "Partial exit; the remaining entry quantity is still open."

        price_direction = self._price_direction(entry_price, exit_price)
        signal_direction = self._signal_direction(decision.final_signal)
        if (
            base.status == DecisionOutcomeStatus.CLOSED
            and price_direction != 0
            and signal_direction != 0
        ):
            base.decision_hit = signal_direction == price_direction

        base.agent_hits = self._agent_hits(decision, price_direction if fully_closed else None)
        return base

    # ------------------------------------------------------------------
    # Aggregations
    # ------------------------------------------------------------------

    def _by_agent(
        self,
        decisions: list[CoordinatorDecision],
        outcome_by_decision: dict[UUID, DecisionOutcome],
    ) -> list[AgentPerformance]:
        stats: dict[str, _AgentStats] = defaultdict(_AgentStats)
        for decision in decisions:
            outcome = outcome_by_decision.get(decision.id)
            if outcome is None or outcome.status != DecisionOutcomeStatus.CLOSED:
                continue
            for output in decision.agent_outputs:
                if output.agent_type == AgentType.RISK_MANAGER:
                    continue
                agent_type = output.agent_type.value
                item = stats[agent_type]
                item.decisions_scored += 1
                item.outputs += 1
                item.confidence_sum += output.confidence
                item.latency_sum += output.processing_time_ms
                hit = outcome.agent_hits.get(agent_type)
                if hit is True:
                    item.correct += 1
                elif hit is False:
                    item.incorrect += 1
                else:
                    item.abstained += 1

        result: list[AgentPerformance] = []
        for agent_type, item in sorted(stats.items()):
            scored = item.correct + item.incorrect
            result.append(
                AgentPerformance(
                    agent_type=agent_type,
                    decisions_scored=item.decisions_scored,
                    correct=item.correct,
                    incorrect=item.incorrect,
                    abstained=item.abstained,
                    hit_rate_pct=(item.correct / scored * 100.0) if scored else 0.0,
                    avg_confidence=(item.confidence_sum / item.outputs if item.outputs else 0.0),
                    avg_processing_time_ms=(
                        item.latency_sum / item.outputs if item.outputs else 0.0
                    ),
                )
            )
        return result

    def _by_signal(self, outcomes: list[DecisionOutcome]) -> list[SignalPerformance]:
        stats: dict[str, _SignalStats] = defaultdict(_SignalStats)
        for outcome in outcomes:
            item = stats[outcome.signal]
            item.decisions += 1
            if outcome.status == DecisionOutcomeStatus.CLOSED:
                item.net_pnl += outcome.net_pnl
                if outcome.decision_hit is not None:
                    item.resolved += 1
                    if outcome.decision_hit:
                        item.correct += 1

        return [
            SignalPerformance(
                signal=signal,
                decisions=item.decisions,
                resolved=item.resolved,
                correct=item.correct,
                hit_rate_pct=(item.correct / item.resolved * 100.0) if item.resolved else 0.0,
                net_pnl=item.net_pnl,
            )
            for signal, item in sorted(stats.items())
        ]

    def _by_symbol(self, outcomes: list[DecisionOutcome]) -> list[SymbolPerformance]:
        stats: dict[str, _SymbolStats] = defaultdict(_SymbolStats)
        for outcome in outcomes:
            item = stats[outcome.symbol]
            item.decisions += 1
            if outcome.status == DecisionOutcomeStatus.CLOSED:
                item.resolved += 1
                item.net_pnl += outcome.net_pnl
                if outcome.outcome == TradeOutcome.WIN:
                    item.wins += 1

        return [
            SymbolPerformance(
                symbol=symbol,
                decisions=item.decisions,
                resolved=item.resolved,
                win_rate_pct=(item.wins / item.resolved * 100.0) if item.resolved else 0.0,
                net_pnl=item.net_pnl,
            )
            for symbol, item in sorted(stats.items())
        ]

    def _confidence_buckets(self, outcomes: list[DecisionOutcome]) -> list[ConfidenceBucket]:
        counts = [0 for _ in _BUCKETS]
        correct = [0 for _ in _BUCKETS]
        for outcome in outcomes:
            if outcome.status != DecisionOutcomeStatus.CLOSED or outcome.decision_hit is None:
                continue
            index = self._bucket_index(outcome.confidence)
            counts[index] += 1
            if outcome.decision_hit:
                correct[index] += 1

        return [
            ConfidenceBucket(
                lower=lower,
                upper=upper,
                decisions=counts[index],
                correct=correct[index],
                hit_rate_pct=(correct[index] / counts[index] * 100.0 if counts[index] else 0.0),
                minimum_sample_size=_MIN_SCORE_BUCKET_SAMPLE,
                sample_sufficient=counts[index] >= _MIN_SCORE_BUCKET_SAMPLE,
            )
            for index, (lower, upper) in enumerate(_BUCKETS)
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filled_qty(order: Order) -> Decimal:
        return order.filled_quantity if order.filled_quantity > 0 else order.quantity

    @staticmethod
    def _fee_in_quote(order: Order) -> Decimal:
        fee = order.commission or _ZERO
        if fee == 0:
            return _ZERO
        asset = (order.commission_asset or "").upper()
        base, _, quote = order.symbol.partition("/")
        if asset == quote.upper():
            return fee
        if asset == base.upper() and order.average_fill_price is not None:
            return fee * order.average_fill_price
        # BNB or another third asset requires an external conversion; do not
        # silently pretend it is quote-denominated.
        return _ZERO

    @staticmethod
    def _price_direction(entry: Decimal | None, exit_: Decimal | None) -> int:
        if entry is None or exit_ is None:
            return 0
        if exit_ > entry:
            return 1
        if exit_ < entry:
            return -1
        return 0

    @staticmethod
    def _signal_direction(signal: SignalDirection | str) -> int:
        value = signal.value if isinstance(signal, SignalDirection) else str(signal).lower()
        if value in {SignalDirection.STRONG_BUY.value, SignalDirection.BUY.value}:
            return 1
        if value in {SignalDirection.STRONG_SELL.value, SignalDirection.SELL.value}:
            return -1
        return 0

    @staticmethod
    def _agent_hits(
        decision: CoordinatorDecision,
        price_direction: int | None,
    ) -> dict[str, bool | None]:
        hits: dict[str, bool | None] = {}
        for output in decision.agent_outputs:
            agent_type = output.agent_type.value
            if output.agent_type == AgentType.RISK_MANAGER:
                hits[agent_type] = None
                continue
            direction = LearningService._signal_direction(output.signal)
            if price_direction in (None, 0) or direction == 0:
                hits[agent_type] = None
            else:
                hits[agent_type] = direction == price_direction
        return hits

    @staticmethod
    def _bucket_index(confidence: float) -> int:
        for index, (_, upper) in enumerate(_BUCKETS):
            if confidence < upper:
                return index
        return len(_BUCKETS) - 1
