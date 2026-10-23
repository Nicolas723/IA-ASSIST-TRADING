"""SMTP email notification channel."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING

from src.domain.notifications.ports import NotificationPort
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.notifications.entities import Alert

logger = get_logger(__name__)


class EmailChannel(NotificationPort):
    """Send alerts through a plain SMTP server (runs blocking I/O in a thread)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addr: str,
        timeout: float = 10.0,
    ) -> None:
        self._host = host.strip()
        self._port = port
        self._username = username.strip()
        self._password = password
        self._from = (from_addr or username).strip()
        self._recipients = [addr.strip() for addr in to_addr.split(",") if addr.strip()]
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "email"

    @property
    def is_configured(self) -> bool:
        return bool(self._host and self._from and self._recipients)

    def _send_sync(self, alert: Alert) -> None:
        message = EmailMessage()
        message["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"
        message["From"] = self._from
        message["To"] = ", ".join(self._recipients)
        message.set_content(alert.message)

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            server.starttls()
            if self._username:
                server.login(self._username, self._password)
            server.send_message(message)

    async def send(self, alert: Alert) -> bool:
        if not self.is_configured:
            return False
        try:
            await asyncio.to_thread(self._send_sync, alert)
            return True
        except Exception as exc:
            logger.warning("email_alert_error", error=str(exc))
            return False
