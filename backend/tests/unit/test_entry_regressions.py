"""Regressions that silently blocked every real entry of a 14 h Testnet session."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.autonomous_engine import AutonomousBotConfig, AutonomousEngine
from src.application.services.risk_service import RiskService
from src.domain.agents.entities import CoordinatorDecision, SignalDirection
from src.domain.market.entities import Exchange
from src.domain.trading.entities import ExecutionMode, Portfolio
from src.shared.config.settings import MAX_SCAN_SYMBOLS, LLMScanMode

MAX_NOTIONAL = Decimal("50")


def _engine(
    monkeypatch: pytest.MonkeyPatch, **settings: object
) -> tuple[AutonomousEngine, MagicMock]:
    monkeypatch.setattr(
        "src.application.services.autonomous_engine.get_settings",
        lambda: SimpleNamespace(
            real_trading_max_notional=MAX_NOTIONAL,
            llm_scan_mode=LLMScanMode.NORMAL,
            correlation_guard_enabled=False,
            **settings,
        ),
    )
    live = MagicMock()
    live.get_portfolio.return_value = Portfolio(
        mode=ExecutionMode.REAL,
        exchange=Exchange.BINANCE,
        total_balance=Decimal("10007.75"),
        available_balance=Decimal("10007.75"),
        locked_balance=Decimal("0"),
        positions=[],
    )
    live.get_positions.return_value = []
    live.preflight_reconciliation = AsyncMock(return_value=(True, None))
    live.place_order = AsyncMock(return_value=MagicMock(id="order"))
    engine = AutonomousEngine(risk_service=RiskService(), live_trading_service=live)
    engine.config = AutonomousBotConfig(
        execution_mode="real",
        confirm_real_trading=True,
        min_confidence_pct=50.0,
        risk_per_trade_pct=3.0,
    )
    return engine, live


async def _buy(engine: AutonomousEngine, live: MagicMock, symbol: str, price: float) -> None:
    await engine._apply_decision(
        symbol=symbol,
        current_price=price,
        decision=CoordinatorDecision(
            symbol=symbol,
            final_signal=SignalDirection.STRONG_BUY,
            aggregate_confidence=0.72,
            consensus_level=1.0,
            reasoning_summary="Committee approved a long entry for this regression test.",
            risk_approved=True,
        ),
        service=live,
        mode=ExecutionMode.REAL,
        market_service=MagicMock(),
        update_last=False,
    )


@pytest.mark.parametrize("price", [1.3296, 4.677, 0.9385, 80178.0, 3.0001])
async def test_capped_quantity_never_exceeds_the_notional_cap(
    monkeypatch: pytest.MonkeyPatch, price: float
) -> None:
    engine, live = _engine(monkeypatch)

    await _buy(engine, live, "XRP/USDT", price)

    req = live.place_order.call_args.kwargs["req"]
    # Same arithmetic as LiveTradingService._assert_order_allowed.
    assert Decimal(str(price)) * Decimal(str(req.quantity)) <= MAX_NOTIONAL


@pytest.mark.parametrize(("symbol", "price"), [("ENA/USDT", 0.2084), ("PEPE/USDT", 0.0000062)])
async def test_low_priced_markets_keep_their_stop_and_target(
    monkeypatch: pytest.MonkeyPatch, symbol: str, price: float
) -> None:
    engine, live = _engine(monkeypatch)

    await _buy(engine, live, symbol, price)

    req = live.place_order.call_args.kwargs["req"]
    assert req.stop_loss == pytest.approx(price * 0.98)
    assert req.take_profit == pytest.approx(price * 1.04)


def test_universe_keeps_every_market_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    markets = [f"C{i}/USDT" for i in range(MAX_SCAN_SYMBOLS)]
    engine, _live = _engine(monkeypatch, shadow_scan_symbols=markets)
    engine.config.symbol = markets[0]

    assert engine._resolve_scan_universe([]) == markets
