"""UI-managed integration settings, encrypted store, account snapshot and heartbeat manager."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from src.application.services.account_snapshot_service import AccountSnapshotService
from src.application.services.heartbeat_service import HeartbeatManager
from src.application.services.integration_settings_service import (
    IntegrationSettingsService,
    LLMProviderUpdate,
    LLMSettingsUpdate,
    TelegramSettingsUpdate,
)
from src.infrastructure.security.secure_settings_store import SecureSettingsStore
from src.shared.config.settings import LLMProvider, Settings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".secure_settings.json"
    monkeypatch.setenv("SECURE_SETTINGS_PATH", str(path))
    return path


def test_secure_store_round_trip_is_encrypted(store_path: Path) -> None:
    store = SecureSettingsStore()
    store.set_section("telegram", {"bot_token": "123456:SECRET-TOKEN", "chat_id": "42"})

    raw = store_path.read_text(encoding="utf-8")
    assert "SECRET-TOKEN" not in raw
    assert "ciphertext" in json.loads(raw)

    reloaded = SecureSettingsStore()
    assert reloaded.section("telegram")["bot_token"] == "123456:SECRET-TOKEN"


def test_secure_store_does_not_write_without_explicit_path_under_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SECURE_SETTINGS_PATH", raising=False)
    target = tmp_path / "real_store.json"
    store = SecureSettingsStore(storage_path=target)

    store.set_section("heartbeat", {"ping_url": "https://example.invalid"})

    assert not target.exists()
    assert store.section("heartbeat")["ping_url"] == "https://example.invalid"


@pytest.mark.usefixtures("store_path")
def test_secure_store_rejects_unknown_section() -> None:
    with pytest.raises(ValueError):
        SecureSettingsStore().section("exchange")


@pytest.mark.usefixtures("store_path")
def test_update_llm_masks_key_and_drops_primary_from_fallbacks() -> None:
    settings = Settings()
    service = IntegrationSettingsService(settings=settings, store=SecureSettingsStore())

    view = service.update_llm(
        LLMSettingsUpdate(
            providers=[
                LLMProviderUpdate(provider=LLMProvider.GROQ, api_key="gsk_abcdefghijklmnop")
            ],
            primary_provider=LLMProvider.GROQ,
            fallback_providers=[LLMProvider.GROQ, LLMProvider.GOOGLE, LLMProvider.GOOGLE],
        )
    )

    assert settings.groq.api_key == "gsk_abcdefghijklmnop"
    assert settings.llm_primary_provider == LLMProvider.GROQ
    assert settings.llm_fallback_providers == [LLMProvider.GOOGLE]
    groq = next(item for item in view.llm.providers if item.provider == LLMProvider.GROQ)
    assert groq.configured is True
    assert groq.masked_key == "gsk_…mnop"
    assert "abcdefgh" not in view.model_dump_json()


@pytest.mark.usefixtures("store_path")
def test_persisted_llm_settings_apply_to_fresh_settings() -> None:
    IntegrationSettingsService(settings=Settings(), store=SecureSettingsStore()).update_llm(
        LLMSettingsUpdate(
            providers=[LLMProviderUpdate(provider=LLMProvider.DEEPSEEK, model="deepseek-reasoner")]
        )
    )

    fresh = Settings()
    IntegrationSettingsService(settings=fresh, store=SecureSettingsStore()).apply_persisted()

    assert fresh.deepseek.default_model == "deepseek-reasoner"


@pytest.mark.usefixtures("store_path")
def test_update_telegram_keeps_token_when_omitted_and_clears_on_request() -> None:
    settings = Settings()
    service = IntegrationSettingsService(settings=settings, store=SecureSettingsStore())

    service.update_telegram(TelegramSettingsUpdate(bot_token="111:AAA-secret-token", chat_id="7"))
    service.update_telegram(TelegramSettingsUpdate(chat_id="8"))
    assert settings.telegram_bot_token == "111:AAA-secret-token"
    assert settings.telegram_chat_id == "8"

    view = service.update_telegram(TelegramSettingsUpdate(clear_token=True))
    assert settings.telegram_bot_token == ""
    assert view.telegram.configured is False


async def test_heartbeat_manager_restart_and_stop() -> None:
    manager = HeartbeatManager()

    await manager.restart(url="")
    assert manager.running is False

    await manager.restart(url="https://example.invalid/ping", interval_seconds=3600)
    assert manager.running is True

    await manager.stop()
    assert manager.running is False
    assert manager.service is None


class _FakeLive:
    def __init__(self, details: dict[str, dict[str, float]]) -> None:
        self.details = details

    async def account_details(self, _exchange: Any = None) -> dict[str, dict[str, float]]:
        return self.details

    def status(self, _exchange: Any = None) -> Any:
        return SimpleNamespace(exchange="binance", testnet=True)

    def get_portfolio(self, _mode: Any) -> Any:
        return SimpleNamespace(
            total_realized_pnl=Decimal("5"),
            positions=[SimpleNamespace(unrealized_pnl=Decimal("-2"))],
        )


async def test_account_snapshot_values_assets_and_tracks_baseline() -> None:
    calls: list[str] = []

    async def price_lookup(symbol: str) -> float | None:
        calls.append(symbol)
        return {"BTC/USDT": 50_000.0}.get(symbol)

    live = _FakeLive(
        {
            "USDT": {"total": 1_000.0, "free": 900.0, "used": 100.0},
            "BTC": {"total": 0.1, "free": 0.1, "used": 0.0},
            "JUNK": {"total": 3.0, "free": 3.0, "used": 0.0},
        }
    )
    service = AccountSnapshotService(live_service=live, price_lookup=price_lookup)

    first = await service.snapshot()
    assert first.equity_usdt == pytest.approx(6_000.0)
    assert first.unpriced_assets == ["JUNK"]
    assert first.total_pnl == pytest.approx(3.0)
    assert first.baseline_equity == pytest.approx(6_000.0)
    assert first.change_usdt == pytest.approx(0.0)

    live.details["USDT"]["total"] = 1_600.0
    second = await service.snapshot()
    assert second.change_usdt == pytest.approx(600.0)
    assert second.change_pct == pytest.approx(10.0)
    # The unpriced asset is cached as such and not looked up again on the next poll.
    assert calls.count("JUNK/USDT") == 1

    reset = await service.reset_baseline()
    assert reset.baseline_equity == pytest.approx(6_600.0)
    assert reset.change_usdt == pytest.approx(0.0)
