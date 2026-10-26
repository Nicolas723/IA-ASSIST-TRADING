"""Unit tests for the outbound heartbeat (external dead-man's switch)."""

from __future__ import annotations

import asyncio

import pytest

from src.application.services.heartbeat_service import HeartbeatService


@pytest.mark.asyncio
async def test_disabled_heartbeat_does_not_ping() -> None:
    calls = 0

    async def ping() -> bool:
        nonlocal calls
        calls += 1
        return True

    service = HeartbeatService(url="", ping=ping)
    await service.start()
    await asyncio.sleep(0)
    await service.stop()

    assert service.enabled is False
    assert await service.ping_once() is False
    assert calls == 0


@pytest.mark.asyncio
async def test_start_and_stop_run_the_ping_loop() -> None:
    pings = 0
    first_ping = asyncio.Event()

    async def ping() -> bool:
        nonlocal pings
        pings += 1
        first_ping.set()
        return True

    service = HeartbeatService(url="https://example.test/ping", interval_seconds=3600, ping=ping)
    await service.start()
    await asyncio.wait_for(first_ping.wait(), timeout=2)
    await service.stop()

    assert service.enabled is True
    assert pings >= 1


@pytest.mark.asyncio
async def test_ping_failure_is_reported_without_raising() -> None:
    async def ping() -> bool:
        return False

    service = HeartbeatService(url="https://example.test/ping", ping=ping)

    assert await service.ping_once() is False


@pytest.mark.asyncio
async def test_http_ping_rejects_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 503

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("src.application.services.heartbeat_service.httpx.AsyncClient", FakeClient)
    service = HeartbeatService(url="https://example.test/ping")

    assert await service.ping_once() is False
