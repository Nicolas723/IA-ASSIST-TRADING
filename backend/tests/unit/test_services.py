"""
Unit tests for MarketDataService, TradingService, and VaultService.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.market_data_service import MarketDataService
from src.application.services.trading_service import TradingService
from src.application.services.vault_service import VaultService
from src.domain.market.entities import OHLCV, Candle, Exchange, Timeframe
from src.domain.trading.entities import ExecutionMode, Order, OrderSide, OrderStatus, OrderType
from src.infrastructure.exchange.paper import PlaceOrderRequest


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
