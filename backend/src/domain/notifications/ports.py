"""Notification ports (interfaces)."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.notifications.entities import Alert


class NotificationPort(abc.ABC):
    """Transport used to deliver an alert to an external channel."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable channel identifier (e.g. ``telegram``)."""

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Whether the channel has enough settings to attempt delivery."""

    @abc.abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Deliver the alert. Returns ``True`` on success; never raises."""
