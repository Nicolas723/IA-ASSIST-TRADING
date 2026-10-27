"""
Integration tests for FastAPI REST API endpoints.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_status(async_client: AsyncClient):
    response = await async_client.get("/api/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert "redis" in data or "status" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_market_symbols(async_client: AsyncClient):
    response = await async_client.get("/api/v1/market/symbols")
    assert response.status_code == 200
    data = response.json()
    assert "symbols" in data
    assert len(data["symbols"]) > 0
    assert "BTC/USDT" in data["symbols"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_agents_list(async_client: AsyncClient):
    response = await async_client.get("/api/v1/agents/list")
    assert response.status_code == 200
    agents = response.json()
    assert isinstance(agents, list)
    assert len(agents) >= 4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_portfolio_state(async_client: AsyncClient):
    response = await async_client.get("/api/v1/portfolio")
    assert response.status_code == 200
    data = response.json()
    assert "total_balance" in data
    assert "available_balance" in data
    assert "positions" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_vault_status(async_client: AsyncClient):
    response = await async_client.get("/api/v1/vault/status")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # Ciphertexts must never be sent to the browser.
    for item in data:
        assert "encrypted_api_key" not in item
        assert "encrypted_secret" not in item


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_observability_mlops(async_client: AsyncClient):
    response = await async_client.get("/api/v1/observability/mlops")
    assert response.status_code == 200
    data = response.json()
    assert "total_tokens_consumed" in data
    assert "active_llm_provider" in data
    assert "agents" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_autonomous_status_and_control(async_client: AsyncClient):
    # 1. Get initial status
    res = await async_client.get("/api/v1/autonomous/status")
    assert res.status_code == 200
    status = res.json()
    assert "is_running" in status
    assert "config" in status
    assert "total_cycles" in status

    # 2. Get logs
    logs_res = await async_client.get("/api/v1/autonomous/logs")
    assert logs_res.status_code == 200
    assert isinstance(logs_res.json(), list)

    # 3. Stop bot
    stop_res = await async_client.post("/api/v1/autonomous/stop")
    assert stop_res.status_code == 200
    stop_data = stop_res.json()
    assert stop_data["is_running"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_real_trading_status(async_client: AsyncClient):
    response = await async_client.get("/api/v1/trading/real/status")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "execution_mode" in data
    assert "allowed_symbols" in data
    assert "max_notional" in data
    assert isinstance(data["oco_enabled"], bool)
    # The protection mode follows the local OCO flag. Integration tests must
    # not assume a specific .env value because testnet OCO is opt-in.
    expected_mode = "oco_with_local_fallback" if data["oco_enabled"] else "local_guard"
    assert data["protection_mode"] == expected_mode
    assert data["oco_protected_positions"] == 0
    assert data["oco_discovery_failed_exchanges"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_real_reconcile(async_client: AsyncClient):
    response = await async_client.post("/api/v1/trading/real/reconcile")
    assert response.status_code == 200
    data = response.json()
    assert "removed" in data
    assert "open_positions" in data
    assert "reconciliation_interval_seconds" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_alerts_status(async_client: AsyncClient):
    response = await async_client.get("/api/v1/alerts/status")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "min_severity" in data
    assert isinstance(data["channels"], list)
    assert {channel["name"] for channel in data["channels"]} == {
        "telegram",
        "webhook",
        "email",
    }
