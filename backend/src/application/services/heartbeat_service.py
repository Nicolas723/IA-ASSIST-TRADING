"""Outbound heartbeat for an external dead-man's switch.

A trading process that dies cannot send its own alert. When
``HEARTBEAT_PING_URL`` is configured, this service pings that URL on a fixed
interval; the external monitor (healthchecks.io, UptimeRobot push monitor, a
self-hosted endpoint, etc.) alerts when the pings stop. The URL is
provider-agnostic because all of them accept a plain GET.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import httpx

from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

PingCallable = Callable[[], Awaitable[bool]]


class HeartbeatService:
    """Periodically ping an external URL while the backend is alive."""

    def __init__(
        self,
        *,
        url: str,
        interval_seconds: int = 60,
        ping: PingCallable | None = None,
    ) -> None:
        self._url = url.strip()
        self._interval = max(15, interval_seconds)
        self._ping = ping or self._http_ping
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    @property
    def interval_seconds(self) -> int:
        return self._interval

    async def start(self) -> None:
        """Start the background loop (idempotent)."""
        if not self.enabled:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="heartbeat")
            logger.info("heartbeat_started", interval_seconds=self._interval)

    async def stop(self) -> None:
        """Cancel the background loop and wait for it to finish."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            logger.info("heartbeat_stopped")

    async def ping_once(self) -> bool:
        """Send a single heartbeat; returns whether it was accepted."""
        if not self.enabled:
            return False
        return await self._ping()

    async def _run(self) -> None:
        while True:
            await self.ping_once()
            await asyncio.sleep(self._interval)

    async def _http_ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self._url)
        except Exception as exc:
            logger.warning("heartbeat_ping_failed", error=str(exc))
            return False
        if response.status_code >= 400:
            logger.warning("heartbeat_ping_rejected", status_code=response.status_code)
            return False
        return True


class HeartbeatManager:
    """Own the heartbeat task so the UI can reconfigure it without a restart."""

    def __init__(self) -> None:
        self._service: HeartbeatService | None = None

    @property
    def service(self) -> HeartbeatService | None:
        return self._service

    @property
    def running(self) -> bool:
        return self._service is not None and self._service.enabled

    async def restart(self, *, url: str, interval_seconds: int = 60) -> None:
        """Stop the current loop and start a new one with the given settings."""
        await self.stop()
        if not (url or "").strip():
            logger.info("heartbeat_disabled")
            return
        self._service = HeartbeatService(url=url, interval_seconds=interval_seconds)
        await self._service.start()

    async def stop(self) -> None:
        service = self._service
        self._service = None
        if service is not None:
            await service.stop()


heartbeat_manager = HeartbeatManager()
