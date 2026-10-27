"""Trade notifications: one message when a position opens, one when it closes.

The live trading service used to emit an alert for every step (order filled,
OCO placed, OCO cancelled, position closed, stop triggered), so a single trade
produced four or five messages. These builders produce exactly one structured
alert per trade event. ``metadata["event"]`` lets channels such as Telegram
render a compact visual layout; ``title``/``message`` stay as plain-text
fallback for webhook and email.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from src.domain.notifications.entities import Alert, AlertSeverity

if TYPE_CHECKING:
    from decimal import Decimal

CloseReason = Literal["take_profit", "stop_loss", "signal", "manual", "emergency", "external"]

CLOSE_REASON_LABEL: dict[str, str] = {
    "take_profit": "Objetivo alcanzado",
    "stop_loss": "Stop activado",
    "signal": "Señal del comité",
    "manual": "Cierre manual",
    "emergency": "Parada de emergencia",
    "external": "Cerrada fuera del asistente",
}

TRADE_EVENTS = frozenset({"trade_opened", "trade_closed"})


def money(value: float | Decimal | None) -> str:
    """$1.234,56 — Spanish separators, matching the UI."""
    if value is None:
        return "—"
    text = f"{float(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"${text}"


def _quantity(value: float | Decimal) -> str:
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def _network(testnet: bool) -> str:
    return "Testnet" if testnet else "Mainnet"


def trade_opened(
    *,
    symbol: str,
    quantity: float | Decimal,
    price: float | Decimal,
    stop_loss: float | Decimal | None,
    take_profit: float | Decimal | None,
    testnet: bool,
    protected_by_exchange: bool,
) -> Alert:
    protection = "OCO en Binance" if protected_by_exchange else "vigilancia local"
    return Alert(
        title=f"Compra · {symbol}",
        message=(
            f"{_quantity(quantity)} {symbol} a {money(price)}. "
            f"Objetivo {money(take_profit)} · stop {money(stop_loss)}. "
            f"Protección: {protection}. {_network(testnet)}."
        ),
        severity=AlertSeverity.SUCCESS,
        metadata={
            "event": "trade_opened",
            "symbol": symbol,
            "quantity": float(quantity),
            "price": float(price),
            "stop_loss": float(stop_loss) if stop_loss is not None else None,
            "take_profit": float(take_profit) if take_profit is not None else None,
            "testnet": testnet,
            "protection": "oco" if protected_by_exchange else "local",
        },
    )


def trade_closed(
    *,
    symbol: str,
    quantity: float | Decimal,
    entry_price: float | Decimal | None,
    exit_price: float | Decimal | None,
    reason: CloseReason,
    testnet: bool,
) -> Alert:
    pnl: float | None = None
    pnl_pct: float | None = None
    if entry_price and exit_price:
        pnl = (float(exit_price) - float(entry_price)) * float(quantity)
        pnl_pct = (float(exit_price) / float(entry_price) - 1.0) * 100.0
    outcome = (
        "Ganancia"
        if pnl is not None and pnl > 0
        else "Pérdida"
        if pnl is not None and pnl < 0
        else "Cierre"
    )
    result = (
        f"{'+' if pnl > 0 else ''}{money(pnl)} ({f'{pnl_pct:+.2f}'.replace('.', ',')} %)"
        if pnl is not None and pnl_pct is not None
        else "resultado no disponible"
    )
    return Alert(
        title=f"{outcome} · {symbol}",
        message=(
            f"{result}. {money(entry_price)} → {money(exit_price)}. "
            f"{CLOSE_REASON_LABEL[reason]}. {_network(testnet)}."
        ),
        severity=AlertSeverity.SUCCESS if pnl is not None and pnl >= 0 else AlertSeverity.WARNING,
        metadata={
            "event": "trade_closed",
            "symbol": symbol,
            "quantity": float(quantity),
            "entry_price": float(entry_price) if entry_price is not None else None,
            "exit_price": float(exit_price) if exit_price is not None else None,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "testnet": testnet,
        },
    )
