"""
Correlation-aware sizing guard for new long exposure.

Spot automation is long-only, so a *positive* return correlation with an
existing position concentrates risk, while a negative correlation diversifies
it. This module turns that observation into a small, pure decision that the
autonomous engine can apply before sizing an order:

- ``allow``: no material positive correlation, keep the requested risk.
- ``reduce``: positive correlation above the reduce threshold, shrink risk.
- ``block``: positive correlation above the block threshold, skip the entry.
- ``unavailable``: not enough overlapping closed candles, keep the requested
  risk and let the operator know that the guard could not measure anything.

Correlation is a statistic about past returns, not a probability or a
forecast. The guard only limits exposure; it never creates a signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from src.application.services.concentration_service import (
    pearson_correlation,
    returns_by_timestamp,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from src.domain.market.entities import Candle

CorrelationAction = Literal["allow", "reduce", "block", "unavailable"]

# Never shrink below this fraction: a correlation penalty should temper the
# trade, not silently turn it into dust.
MIN_SIZE_MULTIPLIER = 0.25


@dataclass(frozen=True)
class CorrelationGuardDecision:
    """Outcome of comparing a candidate with the existing portfolio."""

    action: CorrelationAction
    size_multiplier: float
    max_correlation: float | None
    correlated_symbol: str | None
    sample_size: int
    reason: str

    @property
    def blocks(self) -> bool:
        return self.action == "block"


def evaluate_correlation_guard(
    *,
    candidate_symbol: str,
    candidate_candles: Sequence[Candle],
    existing_candles: Mapping[str, Sequence[Candle]],
    reduce_threshold: float,
    block_threshold: float,
    min_samples: int,
) -> CorrelationGuardDecision:
    """Compare the candidate's returns with every other held symbol.

    Only positive correlation is penalised. The pair with the highest positive
    correlation drives the decision, so one concentrated position is enough to
    reduce or block the new entry.
    """
    candidate_returns = returns_by_timestamp(candidate_candles)
    best_correlation: float | None = None
    best_symbol: str | None = None
    best_samples = 0

    for symbol, candles in existing_candles.items():
        if symbol == candidate_symbol:
            continue
        other_returns = returns_by_timestamp(candles)
        shared = sorted(set(candidate_returns).intersection(other_returns))
        if len(shared) < min_samples:
            continue
        correlation = pearson_correlation(
            [candidate_returns[timestamp] for timestamp in shared],
            [other_returns[timestamp] for timestamp in shared],
        )
        if correlation is None:
            continue
        if best_correlation is None or correlation > best_correlation:
            best_correlation = correlation
            best_symbol = symbol
            best_samples = len(shared)

    if best_correlation is None or best_symbol is None:
        return CorrelationGuardDecision(
            action="unavailable",
            size_multiplier=1.0,
            max_correlation=None,
            correlated_symbol=None,
            sample_size=0,
            reason=(
                "No hay suficientes velas cerradas comunes con las posiciones "
                f"abiertas (mínimo {min_samples}); no se aplica penalización."
            ),
        )

    if best_correlation >= block_threshold:
        return CorrelationGuardDecision(
            action="block",
            size_multiplier=0.0,
            max_correlation=best_correlation,
            correlated_symbol=best_symbol,
            sample_size=best_samples,
            reason=(
                f"Correlación positiva {best_correlation:.4f} con {best_symbol} "
                f"(umbral de bloqueo {block_threshold}); entrada bloqueada."
            ),
        )

    if best_correlation >= reduce_threshold:
        span = block_threshold - reduce_threshold
        multiplier = (block_threshold - best_correlation) / span if span > 0 else 1.0
        multiplier = round(max(MIN_SIZE_MULTIPLIER, min(1.0, multiplier)), 2)
        return CorrelationGuardDecision(
            action="reduce",
            size_multiplier=multiplier,
            max_correlation=best_correlation,
            correlated_symbol=best_symbol,
            sample_size=best_samples,
            reason=(
                f"Correlación positiva {best_correlation:.4f} con {best_symbol} "
                f"(umbral de reducción {reduce_threshold}); riesgo reducido al "
                f"{multiplier:.0%}."
            ),
        )

    return CorrelationGuardDecision(
        action="allow",
        size_multiplier=1.0,
        max_correlation=best_correlation,
        correlated_symbol=best_symbol,
        sample_size=best_samples,
        reason=(
            f"Correlación máxima {best_correlation:.4f} con {best_symbol}, "
            f"por debajo del umbral de reducción {reduce_threshold}."
        ),
    )
