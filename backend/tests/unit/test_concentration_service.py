"""Read-only concentration and correlation report: descriptive, never trading."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.application.services.concentration_service import (
    ConcentrationService,
    build_concentration_report,
    pearson_correlation,
    returns_by_timestamp,
)
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.trading.entities import (
    ExecutionMode,
    Portfolio,
    Position,
    PositionSide,
)
from src.interfaces.api.v1.deps import get_concentration_service

START = datetime(2026, 1, 1, tzinfo=UTC)


def _candles(closes: list[float], symbol: str = "BTC/USDT") -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=Timeframe.H1,
            timestamp=START + timedelta(hours=index),
            ohlcv=OHLCV(
                open=Decimal(str(close)),
                high=Decimal(str(close * 1.001)),
                low=Decimal(str(close * 0.999)),
                close=Decimal(str(close)),
                volume=Decimal("10"),
            ),
        )
        for index, close in enumerate(closes)
    ]


def _portfolio(positions: list[Position], equity: float = 1000.0) -> Portfolio:
    return Portfolio(
        mode=ExecutionMode.PAPER,
        exchange=Exchange.BINANCE,
        total_balance=Decimal(str(equity)),
        available_balance=Decimal(str(equity)),
        locked_balance=Decimal("0"),
        positions=positions,
    )


def _position(symbol: str, entry: float, quantity: float, current: float) -> Position:
    return Position(
        symbol=symbol,
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        side=PositionSide.LONG,
        entry_price=Decimal(str(entry)),
        quantity=Decimal(str(quantity)),
        current_price=Decimal(str(current)),
    )


def test_pearson_correlation_detects_direction_and_rejects_flat_series() -> None:
    assert pearson_correlation([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == 1.0
    assert pearson_correlation([1.0, 2.0, 3.0], [6.0, 4.0, 2.0]) == -1.0
    assert pearson_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert pearson_correlation([1.0], [1.0]) is None


def test_returns_ignore_unclosed_candles() -> None:
    candles = _candles([100.0, 110.0, 121.0])
    candles.append(
        candles[-1].model_copy(update={"is_closed": False, "timestamp": START + timedelta(hours=3)})
    )

    returns = returns_by_timestamp(candles)

    assert len(returns) == 2
    assert returns[START + timedelta(hours=1)] == pytest.approx(0.1)


def test_report_flags_per_asset_concentration_and_high_correlation() -> None:
    btc_closes = [100.0 * (1.01**index) for index in range(40)]
    eth_closes = [50.0 * (1.01**index) for index in range(40)]
    portfolio = _portfolio(
        [
            _position("BTC/USDT", 100.0, 1.0, 100.0),
            _position("ETH/USDT", 50.0, 0.2, 50.0),
        ]
    )

    report = build_concentration_report(
        portfolio=portfolio,
        candles_by_symbol={
            "BTC/USDT": _candles(btc_closes),
            "ETH/USDT": _candles(eth_closes, symbol="ETH/USDT"),
        },
        universe=["BTC/USDT", "ETH/USDT"],
        timeframe="1h",
        lookback=40,
        mode="paper",
        max_position_size_pct=5.0,
        max_portfolio_exposure_pct=100.0,
    )

    assert report.read_only is True
    assert report.affects_trading is False
    assert report.positions[0].symbol == "BTC/USDT"
    assert report.positions[0].within_limit is False
    assert any("límite por activo" in warning for warning in report.warnings)
    assert report.correlations[0].correlation == 1.0
    assert report.correlations[0].high is True
    assert any("correlación" in warning for warning in report.warnings)


def test_report_without_positions_is_within_limits() -> None:
    report = build_concentration_report(
        portfolio=_portfolio([]),
        candles_by_symbol={
            "BTC/USDT": _candles([100.0] * 40),
            "ETH/USDT": _candles([50.0] * 40, symbol="ETH/USDT"),
        },
        universe=["BTC/USDT", "ETH/USDT"],
        timeframe="1h",
        lookback=40,
        mode="paper",
        max_position_size_pct=5.0,
        max_portfolio_exposure_pct=100.0,
    )

    assert report.positions == []
    assert report.gross_exposure_usd == 0.0
    assert not any("límite por activo" in warning for warning in report.warnings)


def test_single_symbol_universe_explains_correlation_requirement() -> None:
    report = build_concentration_report(
        portfolio=_portfolio([]),
        candles_by_symbol={"BTC/USDT": _candles([100.0] * 40)},
        universe=["BTC/USDT"],
        timeframe="1h",
        lookback=40,
        mode="paper",
        max_position_size_pct=5.0,
        max_portfolio_exposure_pct=100.0,
    )

    assert report.correlations == []
    assert any("al menos dos símbolos" in warning for warning in report.warnings)


class _FakeMarket:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Timeframe, int]] = []

    async def get_candles(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit))
        return _candles([100.0 * (1.01**index) for index in range(limit)], symbol=symbol)


class _FakeTrading:
    def __init__(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio

    def get_portfolio(self, mode: ExecutionMode = ExecutionMode.PAPER) -> Portfolio:
        assert mode == ExecutionMode.PAPER
        return self.portfolio


class _FakeLive:
    def get_portfolio(self, mode: ExecutionMode = ExecutionMode.REAL) -> Portfolio:
        raise AssertionError("real portfolio must not be read in paper mode")


class _FakeRisk:
    guard = SimpleNamespace(
        profile=SimpleNamespace(max_position_size_pct=5.0, max_portfolio_exposure_pct=100.0)
    )


@pytest.mark.asyncio
async def test_service_reads_active_portfolio_and_requested_universe(monkeypatch) -> None:
    market = _FakeMarket()
    portfolio = _portfolio([_position("BTC/USDT", 100.0, 0.1, 100.0)])
    monkeypatch.setattr(
        "src.application.services.concentration_service.get_settings",
        lambda: SimpleNamespace(
            execution_mode=ExecutionMode.PAPER,
            real_trading_allowed_symbols=["BTC/USDT"],
        ),
    )
    service = ConcentrationService(
        market_service=market,  # type: ignore[arg-type]
        risk_service=_FakeRisk(),  # type: ignore[arg-type]
        trading_service=_FakeTrading(portfolio),  # type: ignore[arg-type]
        live_trading_service=_FakeLive(),  # type: ignore[arg-type]
    )

    report = await service.analyze(
        symbols=["btc-usdt", "eth_usdt", "BTC/USDT"],
        timeframe=Timeframe.H1,
        lookback=40,
    )

    assert report.universe == ["BTC/USDT", "ETH/USDT"]
    assert report.mode == "paper"
    assert market.calls == [
        ("BTC/USDT", Timeframe.H1, 41),
        ("ETH/USDT", Timeframe.H1, 41),
    ]
    assert report.positions[0].symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_concentration_endpoint_is_read_only(async_client, fastapi_app) -> None:
    report = build_concentration_report(
        portfolio=_portfolio([]),
        candles_by_symbol={},
        universe=["BTC/USDT"],
        timeframe="1h",
        lookback=40,
        mode="paper",
        max_position_size_pct=5.0,
        max_portfolio_exposure_pct=100.0,
    )

    class _FakeService:
        async def analyze(self, **_: object):
            return report

    fastapi_app.dependency_overrides[get_concentration_service] = lambda: _FakeService()
    try:
        response = await async_client.get("/api/v1/risk/concentration")
        invalid = await async_client.get("/api/v1/risk/concentration", params={"timeframe": "7h"})
    finally:
        fastapi_app.dependency_overrides.pop(get_concentration_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["affects_trading"] is False
    assert body["universe"] == ["BTC/USDT"]
    assert invalid.status_code == 400
