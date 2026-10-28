"""UI-managed market universe: up to 25 markets, live in the engine, curated catalogue."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from src.application.services.autonomous_engine import AutonomousEngine
from src.application.services.runtime_config_service import (
    RuntimeConfigService,
    RuntimeTradingConfigUpdate,
)
from src.infrastructure.exchange.market_catalog import fetch_market_catalog
from src.shared.config.settings import MAX_SCAN_SYMBOLS, Settings

if TYPE_CHECKING:
    from pathlib import Path


def test_markets_are_saved_normalized(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    service = RuntimeConfigService(settings=settings, storage_path=tmp_path / "rt.json")

    service.update(
        RuntimeTradingConfigUpdate(
            shadow_scan_symbols=["btc/usdt", "DOGE-USDT", "ada_usdt", "BTC/USDT"],
            real_trading_allowed_symbols=["BTC/USDT", "DOGE/USDT", "ADA/USDT"],
        )
    )

    assert settings.shadow_scan_symbols == ["BTC/USDT", "DOGE/USDT", "ADA/USDT"]
    assert "shadow_scan_symbols" in (tmp_path / "rt.json").read_text(encoding="utf-8")


def test_more_than_the_cap_is_rejected() -> None:
    too_many = [f"C{i}/USDT" for i in range(MAX_SCAN_SYMBOLS + 1)]
    with pytest.raises(PydanticValidationError):
        RuntimeTradingConfigUpdate(shadow_scan_symbols=too_many)


async def test_engine_follows_settings_markets_on_each_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, shadow_scan_symbols=["BTC/USDT", "ETH/USDT"])
    monkeypatch.setattr("src.application.services.autonomous_engine.get_settings", lambda: settings)
    engine = AutonomousEngine()
    engine._universe_from_settings = True

    settings.shadow_scan_symbols = ["BTC/USDT", "ETH/USDT", "DOGE/USDT"]
    # No market service: the cycle stops right after refreshing the universe.
    await engine._run_cycle(None, None)

    assert engine.config.scan_symbols == ["BTC/USDT", "ETH/USDT", "DOGE/USDT"]


async def test_catalog_keeps_curated_markets_sorted_and_flags_testnet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker/24hr"):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "ETHUSDT",
                        "quoteVolume": "400",
                        "openPrice": "100",
                        "lastPrice": "102",
                    },
                    {
                        "symbol": "BTCUSDT",
                        "quoteVolume": "900",
                        "openPrice": "10",
                        "lastPrice": "9",
                    },
                    {
                        "symbol": "USDCUSDT",
                        "quoteVolume": "5000",
                        "openPrice": "1",
                        "lastPrice": "1",
                    },
                    {
                        "symbol": "NVDABUSDT",
                        "quoteVolume": "800",
                        "openPrice": "1",
                        "lastPrice": "1",
                    },
                    {"symbol": "DOGEUSDT", "quoteVolume": "50", "openPrice": "1", "lastPrice": "1"},
                ],
            )
        return httpx.Response(
            200,
            json={
                "symbols": [
                    {"symbol": "BTCUSDT", "status": "TRADING"},
                    {"symbol": "ETHUSDT", "status": "TRADING"},
                    {"symbol": "DOGEUSDT", "status": "BREAK"},
                ]
            },
        )

    options = await fetch_market_catalog(testnet=True, transport=httpx.MockTransport(handler))

    assert [(o.symbol, o.tradable) for o in options] == [
        ("BTC/USDT", True),
        ("ETH/USDT", True),
        ("DOGE/USDT", False),
    ]
    assert options[1].change_pct_24h == 2.0
