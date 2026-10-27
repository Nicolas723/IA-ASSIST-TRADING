"""One message per trade event, compact Telegram layout and repeat suppression."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.application.services.alert_service import AlertService
from src.application.services.trade_alerts import trade_closed, trade_opened
from src.domain.notifications.entities import Alert, AlertSeverity
from src.infrastructure.notifications.telegram import render

if TYPE_CHECKING:
    from pathlib import Path


class _Channel:
    name = "fake"
    is_configured = True

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    async def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True


def test_opened_message_is_short_and_visual() -> None:
    text = render(
        trade_opened(
            symbol="BTC/USDT",
            quantity=0.00061,
            price=80878.85,
            stop_loss=79247.22,
            take_profit=84099.09,
            testnet=True,
            protected_by_exchange=True,
        )
    )
    assert text.splitlines()[0] == "🟢 <b>COMPRA · BTC/USDT</b>"
    assert "$80.878,85" in text
    assert "🎯 $84.099,09" in text and "🛑 $79.247,22" in text
    assert "OCO en Binance" in text and "Testnet" in text
    assert len(text.splitlines()) <= 5


def test_closed_message_shows_result_first() -> None:
    win = render(
        trade_closed(
            symbol="ETH/USDT",
            quantity=1,
            entry_price=100,
            exit_price=104,
            reason="take_profit",
            testnet=False,
        )
    )
    loss = trade_closed(
        symbol="ETH/USDT",
        quantity=1,
        entry_price=100,
        exit_price=98,
        reason="stop_loss",
        testnet=False,
    )
    assert win.splitlines()[:2] == ["✅ <b>GANANCIA · ETH/USDT</b>", "<b>+$4,00</b>  (+4,00 %)"]
    assert "Objetivo alcanzado" in win and "Mainnet" in win
    assert render(loss).startswith("🔻 <b>PÉRDIDA · ETH/USDT</b>")
    assert loss.metadata["pnl"] == -2.0
    assert loss.severity == AlertSeverity.WARNING


async def test_repeated_warning_is_sent_once_even_after_restart(tmp_path: Path) -> None:
    store = tmp_path / "dedupe.json"
    first, second = _Channel(), _Channel()
    warning = Alert(title="OCO no colocada", message="BTC/USDT", severity=AlertSeverity.WARNING)

    service = AlertService(channels=[first], enabled=True, dedupe_path=store)
    assert await service.send(warning) is True
    assert await service.send(warning) is False

    # A new process (backend restart) reads the persisted window.
    restarted = AlertService(channels=[second], enabled=True, dedupe_path=store)
    assert await restarted.send(warning) is False
    assert len(first.sent) == 1 and second.sent == []


async def test_trades_and_tests_are_never_suppressed(tmp_path: Path) -> None:
    channel = _Channel()
    service = AlertService(channels=[channel], enabled=True, dedupe_path=tmp_path / "d.json")
    trade = trade_opened(
        symbol="BTC/USDT",
        quantity=1,
        price=1,
        stop_loss=None,
        take_profit=None,
        testnet=True,
        protected_by_exchange=False,
    )
    test = Alert(title="Prueba", message="x", metadata={"event": "test"})
    for _ in range(2):
        assert await service.send(trade) is True
        assert await service.send(test) is True
    assert len(channel.sent) == 4
