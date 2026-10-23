"""Generic JSON webhook notification channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from src.domain.notifications.ports import NotificationPort
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.notifications.entities import Alert

logger = get_logger(__name__)


class WebhookChannel(NotificationPort):
    """POST alerts as JSON to an arbitrary automation endpoint."""

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self._url = url.strip()
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "webhook"

    @property
    def is_configured(self) -> bool:
        return bool(self._url)

    async def send(self, alert: Alert) -> bool:
        if not self.is_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json=alert.model_dump(mode="json"),
                )
            if response.status_code >= 400:
                logger.warning(
                    "webhook_alert_failed",
                    status_code=response.status_code,
                    detail=response.text[:200],
                )
                return False
            return True
        except Exception as exc:
            logger.warning("webhook_alert_error", error=str(exc))
            return False
