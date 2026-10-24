"""
Unit tests for MarketDataService, TradingService, and VaultService.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.market_data_service import MarketDataService
from src.application.services.risk_service import RiskService
from src.application.services.trading_service import TradingService
from src.application.services.vault_service import VaultService
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.trading.entities import ExecutionMode, Order, OrderSide, OrderStatus, OrderType
from src.infrastructure.exchange.paper import PaperExchange, PlaceOrderRequest
from src.shared.exceptions.domain import RiskViolationError


def create_sample_candle() -> Candle:
    now = datetime.now(tz=UTC)
    return Candle(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.H1,
        timestamp=now,
        ohlcv=OHLCV(
            open=Decimal("65000.0"),
            high=Decimal("66000.0"),
            low=Decimal("64500.0"),
            close=Decimal("65800.0"),
            volume=Decimal("100.0"),
        ),
    )


@pytest.mark.asyncio
async def test_market_data_service_get_candles_from_repo():
    mock_exchange = AsyncMock()
    mock_repo = AsyncMock()

    sample_candle = create_sample_candle()
    mock_repo.get_candles.return_value = [sample_candle]

    service = MarketDataService(exchange=mock_exchange, repository=mock_repo)

    result = await service.get_candles("BTC/USDT", Timeframe.H1, limit=50)

    assert len(result) == 1
    assert result[0].symbol == "BTC/USDT"
    mock_repo.get_candles.assert_called_once()


def test_trading_service_place_order():
    mock_paper_exchange = MagicMock()

    sample_order = Order(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        mode=ExecutionMode.PAPER,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        status=OrderStatus.FILLED,
        filled_price=Decimal("65000.0"),
    )
    mock_paper_exchange.place_order.return_value = sample_order
    mock_paper_exchange.positions = []

    service = TradingService(paper_exchange=mock_paper_exchange, risk_service=None)

    req = PlaceOrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
    )

    result = service.place_order(req, current_market_price=65000.0)

    assert result.status == OrderStatus.FILLED
    assert result.symbol == "BTC/USDT"


def test_paper_daily_equity_loss_updates_risk_state_and_survives_restore():
    paper = PaperExchange(initial_balance=10_000, fee_rate=0, slippage=0)
    risk = RiskService()
    service = TradingService(paper_exchange=paper, risk_service=risk)
    request = PlaceOrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("102"),
    )

    service.place_order(request, current_market_price=100)
    service.update_market_prices({"BTC/USDT": 98})
    portfolio = service.get_portfolio()

    assert portfolio.daily_pnl == Decimal("-2.0")
    assert risk.current_daily_loss_pct == pytest.approx(0.02)
    assert portfolio.gross_exposure_usd == Decimal("98")
    assert risk.current_gross_exposure_usd == pytest.approx(98)
    status = risk.get_status()
    assert status.gross_exposure_usd == pytest.approx(98)
    assert status.current_equity_usd == pytest.approx(9998)
    assert status.max_portfolio_exposure_pct > 0

    restored = PaperExchange(initial_balance=10_000, fee_rate=0, slippage=0)
    restored.restore(
        positions=service.get_positions(),
        orders=service.get_orders(),
        trades=service.get_trades(),
        available_balance=portfolio.available_balance,
        realized_pnl=portfolio.total_realized_pnl,
        peak_equity=portfolio.peak_value,
        daily_pnl=portfolio.daily_pnl,
        snapshot_equity=portfolio.total_balance,
        snapshot_updated_at=portfolio.updated_at,
    )

    assert restored.get_portfolio().daily_pnl == Decimal("-2.0")


def test_paper_preflight_counts_existing_exposure_before_new_buy():
    paper = PaperExchange(initial_balance=10_000, fee_rate=0, slippage=0)
    risk = RiskService()
    service = TradingService(paper_exchange=paper, risk_service=risk)

    service.place_order(
        PlaceOrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("102"),
        ),
        current_market_price=100,
    )

    with pytest.raises(RiskViolationError, match="per-asset allocation"):
        service.place_order(
            PlaceOrderRequest(
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("5"),
                stop_loss=Decimal("99.5"),
                take_profit=Decimal("101"),
            ),
            current_market_price=100,
        )


def test_vault_service_list_credentials():
    mock_vault = MagicMock()
    mock_vault.list_configured_exchanges.return_value = []

    service = VaultService(vault=mock_vault)
    credentials = service.list_credentials()

    assert credentials == []


@pytest.mark.asyncio
async def test_market_data_service_get_latest_price():
    mock_exchange = AsyncMock()
    mock_exchange.fetch_last_price.return_value = Decimal("70000.5")
    mock_repo = AsyncMock()

    service = MarketDataService(exchange=mock_exchange, repository=mock_repo)

    price = await service.get_latest_price("BTC/USDT")

    assert price == Decimal("70000.5")
    mock_exchange.fetch_last_price.assert_awaited_once_with("BTC/USDT")
