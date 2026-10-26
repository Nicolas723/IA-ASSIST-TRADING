"""
Unit tests for the correlation-aware sizing guard.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.application.services.correlation_guard import (
    MIN_SIZE_MULTIPLIER,
    evaluate_correlation_guard,
)
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.shared.config.settings import Settings

_START = 1700000000


def _candles_from_returns(
    returns: list[float], *, symbol: str = "BTC/USDT", start: int = _START
) -> list[Candle]:
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return [
        Candle(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=Timeframe.H1,
            timestamp=datetime.fromtimestamp(start + index * 3600, tz=UTC),
            ohlcv=OHLCV(
                open=Decimal(str(close)),
                high=Decimal(str(close)),
                low=Decimal(str(close)),
                close=Decimal(str(close)),
                volume=Decimal("1"),
            ),
            is_closed=True,
        )
        for index, close in enumerate(closes)
    ]


# Two orthogonal return patterns, repeated twice: 8 samples each.
_PATTERN_A = [0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.01, -0.01]
_PATTERN_C = [0.01, 0.01, -0.01, -0.01, 0.01, 0.01, -0.01, -0.01]


def _evaluate(
    candidate_returns: list[float],
    existing: dict[str, list[float]],
    *,
    reduce_threshold: float = 0.75,
    block_threshold: float = 0.95,
    min_samples: int = 4,
):
    return evaluate_correlation_guard(
        candidate_symbol="BTC/USDT",
        candidate_candles=_candles_from_returns(candidate_returns),
        existing_candles={
            symbol: _candles_from_returns(returns, symbol=symbol)
            for symbol, returns in existing.items()
        },
        reduce_threshold=reduce_threshold,
        block_threshold=block_threshold,
        min_samples=min_samples,
    )


@pytest.mark.unit
def test_guard_unavailable_without_other_positions() -> None:
    decision = _evaluate(_PATTERN_A, {})

    assert decision.action == "unavailable"
    assert decision.blocks is False
    assert decision.size_multiplier == 1.0
    assert decision.max_correlation is None


@pytest.mark.unit
def test_guard_unavailable_when_overlap_is_too_short() -> None:
    decision = _evaluate(_PATTERN_A, {"ETH/USDT": _PATTERN_A}, min_samples=50)

    assert decision.action == "unavailable"
    assert decision.size_multiplier == 1.0


@pytest.mark.unit
def test_guard_allows_uncorrelated_pair() -> None:
    decision = _evaluate(_PATTERN_A, {"ETH/USDT": _PATTERN_C})

    assert decision.action == "allow"
    assert decision.size_multiplier == 1.0
    assert decision.correlated_symbol == "ETH/USDT"
    assert decision.max_correlation == pytest.approx(0.0, abs=1e-4)


@pytest.mark.unit
def test_guard_allows_negative_correlation_as_diversification() -> None:
    inverted = [-value for value in _PATTERN_A]

    decision = _evaluate(_PATTERN_A, {"ETH/USDT": inverted})

    assert decision.action == "allow"
    assert decision.size_multiplier == 1.0
    assert decision.max_correlation == pytest.approx(-1.0)


@pytest.mark.unit
def test_guard_reduces_proportionally_between_thresholds() -> None:
    # 0.8 * A + 0.6 * C is orthogonal to C, so its correlation with A is 0.8.
    blended = [0.8 * left + 0.6 * right for left, right in zip(_PATTERN_A, _PATTERN_C, strict=True)]

    decision = _evaluate(_PATTERN_A, {"ETH/USDT": blended})

    assert decision.action == "reduce"
    assert decision.max_correlation == pytest.approx(0.8, abs=1e-3)
    # (block - corr) / (block - reduce) = (0.95 - 0.8) / 0.2 = 0.75
    assert decision.size_multiplier == pytest.approx(0.75, abs=1e-2)


@pytest.mark.unit
def test_guard_never_reduces_below_floor() -> None:
    # Correlation near the block threshold yields a raw multiplier below the
    # floor, which must be clamped instead of turning the trade into dust.
    nearly_blocked = [
        0.93 * left + 0.3676 * right for left, right in zip(_PATTERN_A, _PATTERN_C, strict=True)
    ]

    decision = _evaluate(_PATTERN_A, {"ETH/USDT": nearly_blocked})

    assert decision.action == "reduce"
    assert decision.size_multiplier == MIN_SIZE_MULTIPLIER


@pytest.mark.unit
def test_guard_blocks_highly_correlated_entry() -> None:
    decision = _evaluate(_PATTERN_A, {"ETH/USDT": list(_PATTERN_A)})

    assert decision.action == "block"
    assert decision.blocks is True
    assert decision.size_multiplier == 0.0
    assert decision.correlated_symbol == "ETH/USDT"
    assert "bloqueada" in decision.reason


@pytest.mark.unit
def test_guard_uses_the_most_correlated_position() -> None:
    decision = _evaluate(
        _PATTERN_A,
        {
            "ETH/USDT": _PATTERN_C,
            "SOL/USDT": list(_PATTERN_A),
        },
    )

    assert decision.action == "block"
    assert decision.correlated_symbol == "SOL/USDT"


@pytest.mark.unit
def test_guard_ignores_candidate_symbol_itself() -> None:
    decision = evaluate_correlation_guard(
        candidate_symbol="BTC/USDT",
        candidate_candles=_candles_from_returns(_PATTERN_A),
        existing_candles={"BTC/USDT": _candles_from_returns(_PATTERN_A)},
        reduce_threshold=0.75,
        block_threshold=0.95,
        min_samples=4,
    )

    assert decision.action == "unavailable"


@pytest.mark.unit
def test_settings_enable_the_guard_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.correlation_guard_enabled is True
    assert settings.correlation_reduce_threshold == 0.75
    assert settings.correlation_block_threshold == 0.95
    assert settings.correlation_min_samples == 30
    assert settings.correlation_lookback == 200


@pytest.mark.unit
def test_settings_reject_block_threshold_not_above_reduce_threshold() -> None:
    with pytest.raises(ValidationError, match="CORRELATION_BLOCK_THRESHOLD"):
        Settings(
            _env_file=None,
            correlation_reduce_threshold=0.8,
            correlation_block_threshold=0.7,
        )
