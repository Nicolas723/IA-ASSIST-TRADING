"""
AlertService — best-effort multi-channel notifications.

Trading must never fail because a notification could not be delivered, so every
method here swallows transport errors after logging them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.application.services.trade_alerts import TRADE_EVENTS
from src.domain.notifications.entities import (
    SEVERITY_RANK,
    Alert,
    AlertSeverity,
)
from src.infrastructure.notifications.email import EmailChannel
from src.infrastructure.notifications.telegram import TelegramChannel
from src.infrastructure.notifications.webhook import WebhookChannel
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.domain.notifications.ports import NotificationPort
    from src.shared.config.settings import Settings

logger = get_logger(__name__)

# Same warning at most once per 6 hours (persisted across restarts).
DEFAULT_DEDUPE_WINDOW_SECONDS = 6 * 3600
_DEFAULT_DEDUPE_FILE = Path(__file__).resolve().parents[3] / "vault" / ".alert_dedupe.json"


def _default_dedupe_path() -> Path | None:
    """File store in production; in-memory under pytest unless a path is set."""
    configured = os.getenv("ALERT_DEDUPE_PATH", "").strip()
    if configured:
        return Path(configured)
    if "pytest" in sys.modules:
        return None
    return _DEFAULT_DEDUPE_FILE


def _build_channels(settings: Settings) -> list[NotificationPort]:
    """Build every notification channel from the current settings."""
    timeout = settings.alert_timeout_seconds
    return [
        TelegramChannel(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            timeout=timeout,
        ),
        WebhookChannel(url=settings.alert_webhook_url, timeout=timeout),
        EmailChannel(
            host=settings.email_smtp_host,
            port=settings.email_smtp_port,
            username=settings.email_username,
            password=settings.email_password,
            from_addr=settings.email_from,
            to_addr=settings.email_to,
            timeout=timeout,
        ),
    ]


def _min_severity(settings: Settings) -> AlertSeverity:
    try:
        return AlertSeverity(settings.alert_min_severity.lower())
    except ValueError:
        return AlertSeverity.INFO


class ChannelStatus(BaseModel):
    name: str
    configured: bool


class AlertStatus(BaseModel):
    enabled: bool
    min_severity: AlertSeverity
    channels: list[ChannelStatus] = Field(default_factory=list)
    sent_count: int = 0
    failed_count: int = 0
    last_sent_at: datetime | None = None
    last_error: str | None = None


class AlertService:
    """Fan out alerts to every configured channel."""

    def __init__(
        self,
        channels: Sequence[NotificationPort] | None = None,
        enabled: bool = False,
        min_severity: AlertSeverity = AlertSeverity.INFO,
        dedupe_window_seconds: float = DEFAULT_DEDUPE_WINDOW_SECONDS,
        dedupe_path: Path | None = None,
    ) -> None:
        self._channels = list(channels or [])
        self._enabled = enabled
        self._min_severity = min_severity
        self._sent_count = 0
        self._failed_count = 0
        self._last_sent_at: datetime | None = None
        self._last_error: str | None = None
        self._dedupe_window = dedupe_window_seconds
        self._dedupe_path = dedupe_path or _default_dedupe_path()
        self._recent: dict[str, float] = self._load_recent()

    @classmethod
    def from_settings(cls, settings: Settings) -> AlertService:
        """Build the service from environment configuration."""
        return cls(
            channels=_build_channels(settings),
            enabled=settings.alerts_enabled,
            min_severity=_min_severity(settings),
        )

    def reload_from_settings(self, settings: Settings) -> None:
        """Rebuild channels in place so existing references stay valid.

        ``LiveTradingService`` keeps a reference to this object; replacing the
        singleton would leave it sending through the old (unconfigured)
        channels.
        """
        self._channels = _build_channels(settings)
        self._enabled = settings.alerts_enabled
        self._min_severity = _min_severity(settings)
        logger.info(
            "alert_channels_reloaded",
            channels=[channel.name for channel in self._channels],
            enabled=self._enabled,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and any(channel.is_configured for channel in self._channels)

    def status(self) -> AlertStatus:
        return AlertStatus(
            enabled=self.enabled,
            min_severity=self._min_severity,
            channels=[
                ChannelStatus(name=channel.name, configured=channel.is_configured)
                for channel in self._channels
            ],
            sent_count=self._sent_count,
            failed_count=self._failed_count,
            last_sent_at=self._last_sent_at,
            last_error=self._last_error,
        )

    async def notify(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        """Convenience wrapper around :meth:`send`."""
        return await self.send(
            Alert(
                title=title,
                message=message,
                severity=severity,
                metadata=metadata or {},
            )
        )

    # ------------------------------------------------------------------
    # Repeat suppression
    # ------------------------------------------------------------------
    def _load_recent(self) -> dict[str, float]:
        if self._dedupe_path is None:
            return {}
        try:
            raw = json.loads(self._dedupe_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def _save_recent(self) -> None:
        if self._dedupe_path is None:
            return
        try:
            self._dedupe_path.parent.mkdir(parents=True, exist_ok=True)
            self._dedupe_path.write_text(json.dumps(self._recent), encoding="utf-8")
        except Exception as exc:
            logger.warning("alert_dedupe_save_failed", error=str(exc))

    def _is_repeat(self, alert: Alert) -> bool:
        """True when the same warning was already sent inside the window.

        Trade events are never suppressed: two identical trades are two trades.
        The window is persisted, so a backend restart does not resend the same
        warning (restarts used to resend every startup/restore alert).
        """
        event = alert.metadata.get("event")
        if event in TRADE_EVENTS or event == "test" or self._dedupe_window <= 0:
            return False
        now = time.time()
        self._recent = {k: t for k, t in self._recent.items() if now - t < self._dedupe_window}
        key = hashlib.sha256(f"{alert.title}\n{alert.message}".encode()).hexdigest()[:16]
        if key in self._recent:
            return True
        self._recent[key] = now
        self._save_recent()
        return False

    async def send(self, alert: Alert) -> bool:
        """Deliver an alert to all configured channels. Never raises."""
        if SEVERITY_RANK[alert.severity] < SEVERITY_RANK[self._min_severity]:
            return False
        if self._is_repeat(alert):
            logger.info("alert_suppressed_repeat", title=alert.title)
            return False

        configured = [channel for channel in self._channels if channel.is_configured]
        if not self._enabled or not configured:
            logger.info(
                "alert_not_delivered",
                title=alert.title,
                severity=alert.severity.value,
                channels=len(configured),
                enabled=self._enabled,
            )
            return False

        results = await asyncio.gather(
            *(channel.send(alert) for channel in configured),
            return_exceptions=True,
        )
        delivered = False
        errors: list[str] = []
        for channel, result in zip(configured, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(f"{channel.name}: {result}")
            elif result:
                delivered = True

        if delivered:
            self._sent_count += 1
            self._last_sent_at = datetime.now(tz=UTC)
            self._last_error = None
        else:
            self._failed_count += 1
            self._last_error = "; ".join(errors) or "all channels failed"

        logger.info(
            "alert_dispatched",
            title=alert.title,
            severity=alert.severity.value,
            delivered=delivered,
            errors=errors,
        )
        return delivered
