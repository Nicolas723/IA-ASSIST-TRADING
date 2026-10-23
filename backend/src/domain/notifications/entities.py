"""
Notification domain entities.

Alerts are deliberately framework-agnostic: the application layer decides
*when* to notify, while infrastructure adapters decide *how* (Telegram,
webhook, email, ...).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AlertSeverity(StrEnum):
    """Ordered severity levels used to filter outbound alerts."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"


# Higher rank == more severe. Used by AlertService for min-severity filtering.
SEVERITY_RANK: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 10,
    AlertSeverity.SUCCESS: 20,
    AlertSeverity.WARNING: 30,
    AlertSeverity.CRITICAL: 40,
}


class Alert(BaseModel):
    """A human-readable notification about a trading or risk event."""

    title: str
    message: str
    severity: AlertSeverity = AlertSeverity.INFO
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
