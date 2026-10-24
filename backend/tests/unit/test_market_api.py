"""Market coverage must describe implemented capability, not aspirational support."""

from __future__ import annotations

import pytest

from src.domain.market.entities import Exchange, Timeframe
from src.infrastructure.exchange.market_data_factory import create_market_data_adapter


@pytest.mark.asyncio
async def test_market_coverage_reports_only_binance_spot_as_supported(async_client) -> None:
    response = await async_client.get("/api/v1/market/coverage")

    assert response.status_code == 200
    body = response.json()
    entries = {entry["asset_class"]: entry for entry in body["entries"]}

    assert body["active_market_data_exchange"] == "binance"
    assert entries["crypto_spot"]["status"] == "supported"
    assert entries["crypto_spot"]["exchanges"] == ["binance"]
    assert entries["crypto_spot"]["execution_available"] is True
    assert entries["crypto_spot_bybit_data"]["status"] == "market_data_supported"
    assert entries["crypto_spot_bybit_data"]["execution_available"] is False
    for asset_class in ("forex", "equities", "indices", "commodities", "crypto_futures"):
        assert entries[asset_class]["status"] == "not_implemented"
        assert entries[asset_class]["execution_available"] is False
    assert body["configured_symbols"]
    assert body["shadow_symbols"]


def test_market_data_factory_selects_supported_public_adapters() -> None:
    binance = create_market_data_adapter("binance")
    bybit = create_market_data_adapter("BYBIT")

    assert binance.exchange_id == Exchange.BINANCE
    assert bybit.exchange_id == Exchange.BYBIT

    with pytest.raises(ValueError, match="Unsupported market-data exchange"):
        create_market_data_adapter("kraken")


@pytest.mark.asyncio
async def test_bybit_adapter_maps_public_candles_without_network() -> None:
    adapter = create_market_data_adapter("bybit")

    async def fake_fetch_ohlcv(*_args, **_kwargs):
        return [[1_700_000_000_000, "100", "110", "90", "105", "42"]]

    adapter._client.fetch_ohlcv = fake_fetch_ohlcv  # type: ignore[method-assign]

    candles = await adapter.fetch_historical_candles("BTC/USDT", Timeframe.H1, limit=1)

    assert len(candles) == 1
    assert candles[0].exchange == Exchange.BYBIT
    assert candles[0].symbol == "BTC/USDT"
    assert candles[0].close == 105
    assert candles[0].is_closed is True
