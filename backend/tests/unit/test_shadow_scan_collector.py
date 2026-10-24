"""Lifecycle and safety-bound tests for the optional shadow collector."""

import asyncio

import pytest

from src.application.services.shadow_scan_collector import ShadowScanCollector


def test_collector_rejects_unbounded_universe_and_fast_interval() -> None:
    with pytest.raises(ValueError, match="one and five"):
        ShadowScanCollector(lambda _session: None, lambda: None, [], 3600)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="one hour"):
        ShadowScanCollector(lambda _session: None, lambda: None, ["BTC/USDT"], 3599)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_collector_stop_cancels_active_cycle() -> None:
    collector = ShadowScanCollector(
        lambda _session: None,
        lambda: None,
        ["BTC/USDT"],
        3600,  # type: ignore[arg-type]
    )
    started = asyncio.Event()

    async def wait_for_cancel() -> None:
        started.set()
        await asyncio.Event().wait()

    collector.collect_once = wait_for_cancel  # type: ignore[method-assign]
    collector.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    assert collector.is_running

    await collector.stop()

    assert not collector.is_running
