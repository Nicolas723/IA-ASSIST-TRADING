"""
AlertService — best-effort multi-channel notifications.

Trading must never fail because a notification could not be delivered, so every
method here swallows transport errors after logging them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

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
    ) -> None:
        self._channels = list(channels or [])
        self._enabled = enabled
        self._min_severity = min_severity
        self._sent_count = 0
        self._failed_count = 0
        self._last_sent_at: datetime | None = None
        self._last_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> AlertService:
        """Build the service from environment configuration."""
        timeout = settings.alert_timeout_seconds
        channels: list[NotificationPort] = [
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
        try:
            min_severity = AlertSeverity(settings.alert_min_severity.lower())
        except ValueError:
            min_severity = AlertSeverity.INFO
        return cls(
            channels=channels,
            enabled=settings.alerts_enabled,
            min_severity=min_severity,
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

    async def send(self, alert: Alert) -> bool:
        """Deliver an alert to all configured channels. Never raises."""
        if SEVERITY_RANK[alert.severity] < SEVERITY_RANK[self._min_severity]:
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
