"""Telegram notification channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from src.domain.notifications.ports import NotificationPort
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.notifications.entities import Alert

logger = get_logger(__name__)

_SEVERITY_LABEL = {
    "info": "INFO",
    "success": "OK",
    "warning": "WARNING",
    "critical": "CRITICAL",
}


class TelegramChannel(NotificationPort):
    """Deliver alerts to a Telegram chat through the Bot API."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0) -> None:
        self._token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send(self, alert: Alert) -> bool:
        if not self.is_configured:
            return False
        label = _SEVERITY_LABEL.get(alert.severity.value, "INFO")
        text = f"[{label}] {alert.title}\n\n{alert.message}"
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json={"chat_id": self._chat_id, "text": text})
            if response.status_code >= 400:
                logger.warning(
                    "telegram_alert_failed",
                    status_code=response.status_code,
                    detail=response.text[:200],
                )
                return False
            return True
        except Exception as exc:
            logger.warning("telegram_alert_error", error=str(exc))
            return False
