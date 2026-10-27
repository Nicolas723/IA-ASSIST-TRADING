"""Telegram notification channel."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

import httpx

from src.domain.notifications.ports import NotificationPort
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.notifications.entities import Alert

logger = get_logger(__name__)

_SEVERITY_ICON = {
    "info": "🔔",
    "success": "✅",
    "warning": "⚠️",
    "critical": "🚨",
}

_REASON = {
    "take_profit": "🎯 Objetivo alcanzado",
    "stop_loss": "🛑 Stop activado",
    "signal": "🧠 Señal del comité",
    "manual": "✋ Cierre manual",
    "emergency": "🚨 Parada de emergencia",
    "external": "↗️ Cerrada fuera del asistente",
}


def _money(value: Any) -> str:
    if value is None:
        return "—"
    text = f"{float(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"${text}"


def _pct(value: Any) -> str:
    return f"{float(value or 0):+.2f} %".replace(".", ",")


def _network(testnet: Any) -> str:
    return "🧪 Testnet" if testnet else "💰 Mainnet"


def _render_opened(data: dict[str, Any]) -> str:
    quantity = f"{float(data.get('quantity') or 0):.8f}".rstrip("0").rstrip(".")
    protection = "🛡 OCO en Binance" if data.get("protection") == "oco" else "🛡 Vigilancia local"
    return "\n".join(
        [
            f"🟢 <b>COMPRA · {escape(str(data.get('symbol')))}</b>",
            f"<b>{_money(data.get('price'))}</b>  ·  <code>{quantity}</code>",
            "",
            f"🎯 {_money(data.get('take_profit'))}    🛑 {_money(data.get('stop_loss'))}",
            f"{protection}  ·  {_network(data.get('testnet'))}",
        ]
    )


def _render_closed(data: dict[str, Any]) -> str:
    pnl = data.get("pnl")
    pct = data.get("pnl_pct")
    symbol = escape(str(data.get("symbol")))
    if pnl is None:
        head = f"⚪ <b>CIERRE · {symbol}</b>"
        result = "Resultado no disponible"
    else:
        win = float(pnl) >= 0
        head = f"{'✅ <b>GANANCIA' if win else '🔻 <b>PÉRDIDA'} · {symbol}</b>"
        result = f"<b>{'+' if win else ''}{_money(pnl)}</b>  ({_pct(pct)})"
    return "\n".join(
        [
            head,
            result,
            "",
            f"{_money(data.get('entry_price'))} → {_money(data.get('exit_price'))}",
            f"{_REASON.get(str(data.get('reason')), '')}  ·  {_network(data.get('testnet'))}",
        ]
    )


def render(alert: Alert) -> str:
    """Compact HTML message: trade events get a visual layout, others a short card."""
    event = alert.metadata.get("event")
    if event == "trade_opened":
        return _render_opened(alert.metadata)
    if event == "trade_closed":
        return _render_closed(alert.metadata)
    prerendered = alert.metadata.get("html")
    if isinstance(prerendered, str) and prerendered:
        return prerendered
    icon = _SEVERITY_ICON.get(alert.severity.value, "🔔")
    return f"{icon} <b>{escape(alert.title)}</b>\n{escape(alert.message)}"


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
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": render(alert),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
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
