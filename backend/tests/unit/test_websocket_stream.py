"""
Unit tests for WebSocket Market Stream interface and ConnectionManager.
"""

from unittest.mock import AsyncMock

import pytest

from src.interfaces.websocket.market_stream import ConnectionManager


@pytest.mark.asyncio
async def test_connection_manager_connect_and_disconnect():
    cm = ConnectionManager()
    mock_ws = AsyncMock()

    await cm.connect("BTC/USDT", mock_ws)

    assert "BTC/USDT" in cm.active_connections
    assert mock_ws in cm.active_connections["BTC/USDT"]
    assert mock_ws in cm.all_connections

    cm.disconnect("BTC/USDT", mock_ws)

    assert "BTC/USDT" not in cm.active_connections
    assert mock_ws not in cm.all_connections


@pytest.mark.asyncio
async def test_connection_manager_broadcast_to_symbol():
    cm = ConnectionManager()
    mock_ws1 = AsyncMock()
    mock_ws2 = AsyncMock()

    await cm.connect("BTC/USDT", mock_ws1)
    await cm.connect("ETH/USDT", mock_ws2)

    payload = {"type": "price_tick", "price": 65000.0}
    await cm.broadcast_to_symbol("BTC/USDT", payload)

    mock_ws1.send_json.assert_called_once_with(payload)
    mock_ws2.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_connection_manager_broadcast_all():
    cm = ConnectionManager()
    mock_ws1 = AsyncMock()
    mock_ws2 = AsyncMock()

    await cm.connect("BTC/USDT", mock_ws1)
    await cm.connect("ETH/USDT", mock_ws2)

    payload = {"type": "heartbeat", "status": "ok"}
    await cm.broadcast_all(payload)

    mock_ws1.send_json.assert_called_once_with(payload)
    mock_ws2.send_json.assert_called_once_with(payload)


@pytest.mark.asyncio
async def test_connection_manager_resolve_price_uses_live_provider():
    cm = ConnectionManager()
    cm.last_prices["BTC/USDT"] = 100.0

    async def provider(_symbol: str) -> float:
        return 65432.1

    cm.set_price_provider(provider)
    assert await cm._resolve_price("BTC/USDT") == 65432.1


@pytest.mark.asyncio
async def test_connection_manager_resolve_price_falls_back_to_simulation():
    cm = ConnectionManager()
    cm.last_prices["BTC/USDT"] = 100.0

    price = await cm._resolve_price("BTC/USDT")
    assert 99.9 <= price <= 100.1
