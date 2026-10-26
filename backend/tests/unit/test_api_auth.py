"""The optional API key must protect /api/* without breaking local defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

import src.main as main_module
from src.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI

COVERAGE_PATH = "/api/v1/market/coverage"


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_api_key_is_not_required_when_token_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.settings, "api_auth_token", "")
    app = create_app()

    async with _client(app) as client:
        response = await client.get(COVERAGE_PATH)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_api_key_rejects_missing_or_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.settings, "api_auth_token", "test-token")
    app = create_app()

    async with _client(app) as client:
        missing = await client.get(COVERAGE_PATH)
        wrong = await client.get(COVERAGE_PATH, headers={"X-API-Key": "wrong"})
        accepted = await client.get(COVERAGE_PATH, headers={"X-API-Key": "test-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_api_key_leaves_health_and_websocket_paths_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.settings, "api_auth_token", "test-token")
    app = create_app()

    async with _client(app) as client:
        health = await client.get("/health")
        # A WebSocket route rejects a plain GET, but it must not be the auth
        # middleware returning 401: reaching the route proves it was exempt.
        websocket_path = await client.get("/api/v1/ws/market/BTC-USDT")

    assert health.status_code == 200
    assert websocket_path.status_code != 401
