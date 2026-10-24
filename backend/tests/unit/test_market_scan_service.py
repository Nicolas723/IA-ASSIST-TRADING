from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.application.services.market_scan_service import (
    MarketScanService,
    ShadowEvaluationService,
    ShadowScanCandidate,
    ShadowScanResult,
)
from src.domain.market.entities import (
    OHLCV,
    Candle,
    Exchange,
    OrderBook,
    OrderBookLevel,
    Timeframe,
)


class FakeMarketDataService:
    def __init__(self, closes: dict[str, list[str]], stale_symbols: set[str] | None = None) -> None:
        self.closes = closes
        self.stale_symbols = stale_symbols or set()
        self.calls: list[str] = []

    async def get_candles(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        self.calls.append(symbol)
        now = datetime.now(tz=UTC)
        values = self.closes.get(symbol, [])[-limit:]
        return [
            Candle(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                timeframe=timeframe,
                timestamp=now
                - timedelta(hours=len(values) - index + (4 if symbol in self.stale_symbols else 0)),
                ohlcv=OHLCV(
                    open=Decimal(value),
                    high=Decimal(value) + Decimal("1"),
                    low=Decimal(value) - Decimal("1"),
                    close=Decimal(value),
                    volume=Decimal("10"),
                ),
            )
            for index, value in enumerate(values)
        ]

    async def get_order_book(self, symbol: str, limit: int = 5) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timestamp=datetime.now(UTC),
            bids=[OrderBookLevel(price=Decimal("99.9"), quantity=Decimal("10"))],
            asks=[OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10"))],
        )


@pytest.mark.asyncio
async def test_shadow_scan_ranks_movement_without_enabling_execution() -> None:
    market = FakeMarketDataService(
        {
            "BTC/USDT": ["100", "103"],
            "ETH/USDT": ["100", "110"],
        }
    )

    result = await MarketScanService(market).scan(["BTC/USDT", "ETH/USDT"])

    assert result.execution_enabled is False
    assert len(market.calls) == 2
    assert result.candidates[1].rank == 1
    assert result.candidates[1].activity_score == 10.0
    assert result.candidates[1].spread_status == "observed"
    assert result.candidates[1].quoted_spread_pct == 0.2
    assert result.candidates[1].observed_movement_less_spread_pct == 9.8
    assert "not expected return" in result.ranking_basis


@pytest.mark.asyncio
async def test_shadow_scan_marks_insufficient_data_and_stale_market_unavailable() -> None:
    market = FakeMarketDataService(
        {
            "BTC/USDT": ["100"],
            "ETH/USDT": ["100", "101"],
        },
        stale_symbols={"ETH/USDT"},
    )

    result = await MarketScanService(market).scan(["BTC/USDT", "ETH/USDT"])

    assert result.candidates[0].status == "insufficient_data"
    assert result.candidates[1].status == "unavailable"
    assert result.candidates[1].activity_score is None


@pytest.mark.asyncio
async def test_shadow_scan_labels_regime_from_closed_candles() -> None:
    closes = [str(100 + index) for index in range(120)]
    market = FakeMarketDataService({"BTC/USDT": closes})

    result = await MarketScanService(market).scan(["BTC/USDT"], lookback_candles=24)

    candidate = result.candidates[0]
    assert candidate.status == "observed"
    assert candidate.regime_status == "classified"
    assert candidate.trend_regime == "uptrend"
    assert candidate.volatility_regime in {"low", "normal", "high"}
    assert "not a forecast" in candidate.regime_disclaimer
    # Movement still uses the requested lookback window, not the 120-candle
    # history fetched for the regime label.
    assert candidate.activity_score == pytest.approx((219 - 196) / 196 * 100, abs=0.01)


@pytest.mark.asyncio
async def test_shadow_scan_keeps_movement_but_does_not_assume_missing_spread_is_zero() -> None:
    class MissingOrderBookMarket(FakeMarketDataService):
        async def get_order_book(self, symbol: str, limit: int = 5) -> OrderBook:
            raise RuntimeError("order book unavailable")

    market = MissingOrderBookMarket({"BTC/USDT": ["100", "102"]})

    result = await MarketScanService(market).scan(["BTC/USDT"])

    candidate = result.candidates[0]
    assert candidate.status == "observed"
    assert candidate.activity_score == 2.0
    assert candidate.quoted_spread_pct is None
    assert candidate.observed_movement_less_spread_pct is None
    assert candidate.spread_status == "unavailable"


@pytest.mark.asyncio
async def test_shadow_scan_excludes_the_still_open_candle() -> None:
    class OpenCandleMarket(FakeMarketDataService):
        async def get_candles(self, symbol: str, timeframe: Timeframe, limit: int):
            now = datetime.now(UTC)
            values = [(now - timedelta(hours=2), "100"), (now - timedelta(hours=1), "102")]
            values.append((now - timedelta(minutes=30), "10000"))
            return [
                Candle(
                    symbol=symbol,
                    exchange=Exchange.BINANCE,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    ohlcv=OHLCV(
                        open=Decimal(value),
                        high=Decimal(value),
                        low=Decimal(value),
                        close=Decimal(value),
                        volume=Decimal("1"),
                    ),
                )
                for timestamp, value in values
            ]

    result = await MarketScanService(OpenCandleMarket({})).scan(["BTC/USDT"])

    candidate = result.candidates[0]
    assert candidate.status == "observed"
    assert candidate.activity_score == 2.0
    assert candidate.last_price == Decimal("102")
    assert candidate.latest_candle_at is not None
    assert candidate.latest_candle_at < datetime.now(UTC) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_shadow_scan_rejects_duplicate_symbols_and_large_universe() -> None:
    service = MarketScanService(FakeMarketDataService({}))

    with pytest.raises(ValueError, match="unique"):
        await service.scan(["BTC/USDT", "BTC/USDT"])
    with pytest.raises(ValueError, match="1 and 5"):
        await service.scan(["BTC/USDT"] * 6)


@pytest.mark.asyncio
async def test_shadow_evaluation_waits_for_maturity_and_measures_bounded_forward_return() -> None:
    base = datetime(2025, 1, 1, tzinfo=UTC)

    class FutureMarket:
        async def get_candles(self, symbol, timeframe, limit, since=None):
            return [
                Candle(
                    symbol=symbol,
                    exchange=Exchange.BINANCE,
                    timeframe=timeframe,
                    timestamp=base + timedelta(hours=25),
                    ohlcv=OHLCV(
                        open=Decimal("110"),
                        high=Decimal("111"),
                        low=Decimal("109"),
                        close=Decimal("110"),
                        volume=Decimal("1"),
                    ),
                )
            ]

    scan = ShadowScanResult(
        timeframe="1h",
        scanned_at=base,
        candidates=[
            ShadowScanCandidate(
                symbol="BTC/USDT",
                status="observed",
                rank=1,
                last_price=Decimal("100"),
                latest_candle_at=base,
                lookback_return_pct=2,
                activity_score=2,
            )
        ],
    )
    evaluator = ShadowEvaluationService(FutureMarket())  # type: ignore[arg-type]

    pending = await evaluator.evaluate(scan, 24, now=base + timedelta(hours=23))
    assert pending.status == "pending"
    assert pending.candidates == []
    assert pending.evaluation_anchor_at == base + timedelta(hours=1)

    incomplete_candle = await evaluator.evaluate(scan, 24, now=base + timedelta(hours=25))
    assert incomplete_candle.candidates[0].status == "missing_future_data"

    complete = await evaluator.evaluate(
        scan, 24, now=base + timedelta(hours=26), round_trip_cost_pct=0.25
    )
    assert complete.status == "complete"
    assert complete.candidates[0].forward_return_pct == 10
    assert complete.candidates[0].net_return_pct == pytest.approx(9.75)
    assert complete.estimated_round_trip_cost_pct == 0.25
    assert complete.top_rank_mean_net_return_pct == pytest.approx(9.75)
    assert complete.net_evidence_status == "insufficient"
    assert complete.top_minus_alternatives_pct is None
    assert complete.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_shadow_evaluation_compares_top_rank_with_same_scan_alternatives() -> None:
    base = datetime(2025, 1, 1, tzinfo=UTC)

    class TwoAssetMarket:
        async def get_candles(self, symbol, timeframe, limit, since=None):
            price = Decimal("110" if symbol == "BTC/USDT" else "90")
            return [
                Candle(
                    symbol=symbol,
                    exchange=Exchange.BINANCE,
                    timeframe=timeframe,
                    timestamp=base + timedelta(hours=1),
                    ohlcv=OHLCV(
                        open=price,
                        high=price + 1,
                        low=price - 1,
                        close=price,
                        volume=Decimal("1"),
                    ),
                )
            ]

    scan = ShadowScanResult(
        timeframe="1h",
        scanned_at=base,
        candidates=[
            ShadowScanCandidate(
                symbol="BTC/USDT", status="observed", rank=1, last_price=Decimal("100")
            ),
            ShadowScanCandidate(
                symbol="ETH/USDT", status="observed", rank=2, last_price=Decimal("100")
            ),
        ],
    )

    result = await ShadowEvaluationService(TwoAssetMarket()).evaluate(
        scan, 1, now=base + timedelta(hours=2), round_trip_cost_pct=0.5
    )  # type: ignore[arg-type]

    assert result.top_rank_mean_return_pct == 10
    assert result.alternatives_mean_return_pct == -10
    assert result.top_minus_alternatives_pct == 20
    assert result.equal_weight_random_baseline_pct == 0
    assert result.top_minus_random_baseline_pct == 10
    assert result.estimated_round_trip_cost_pct == 0.5
    assert result.top_rank_mean_net_return_pct == pytest.approx(9.5)
    assert result.alternatives_mean_net_return_pct == pytest.approx(-10.5)
    assert result.top_minus_alternatives_net_pct == 20
    assert result.equal_weight_random_baseline_net_pct == pytest.approx(-0.5)
    assert result.net_evidence_status == "positive_after_costs"
    assert result.evidence_status == "exploratory"
