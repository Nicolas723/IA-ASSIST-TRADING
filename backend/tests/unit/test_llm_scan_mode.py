"""LLM consumption modes: presets, persistence and live application."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from src.application.services.llm_scan_mode_service import LLMScanModeService
from src.shared.config.settings import (
    LLM_SCAN_MODE_PRESETS,
    LLMScanMode,
    Settings,
    llm_scan_mode_preset,
    load_runtime_llm_overrides,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_presets_scale_market_breadth_and_budgets() -> None:
    savings = llm_scan_mode_preset(LLMScanMode.SAVINGS)
    normal = llm_scan_mode_preset(LLMScanMode.NORMAL)
    high = llm_scan_mode_preset(LLMScanMode.HIGH)

    assert savings.max_markets == 1
    assert savings.parallel is False
    assert normal.max_markets == 3
    assert normal.parallel is False
    assert high.max_markets > normal.max_markets
    assert high.parallel is True

    # Budgets must grow with breadth, otherwise the wider mode could never
    # finish the analyses it promises.
    for field in (
        "llm_cycle_request_budget",
        "llm_daily_request_budget",
        "llm_cycle_token_budget",
        "llm_daily_token_budget",
    ):
        assert getattr(savings, field) < getattr(normal, field) < getattr(high, field)

    # Two LLM specialists per committee; the risk manager is deterministic.
    assert high.estimated_llm_calls_per_analysis == high.max_markets * 2


def test_unknown_mode_falls_back_to_savings() -> None:
    assert llm_scan_mode_preset("unknown") is LLM_SCAN_MODE_PRESETS[LLMScanMode.SAVINGS]  # type: ignore[arg-type]


def test_snapshot_exposes_every_selectable_preset(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    service = LLMScanModeService(settings=settings, storage_path=tmp_path / "llm.json")

    snapshot = service.snapshot()

    assert snapshot.mode is LLMScanMode.SAVINGS
    assert [preset.mode for preset in snapshot.presets] == list(LLM_SCAN_MODE_PRESETS)
    assert snapshot.active.max_markets == 1
    assert snapshot.runtime_override_active is False
    # The snapshot reports the caps actually in force, not the preset defaults.
    assert snapshot.effective_budgets["llm_cycle_request_budget"] == (
        settings.llm_cycle_request_budget
    )


def test_update_persists_and_applies_the_mode_in_place(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    path = tmp_path / "vault" / ".runtime_llm_config.json"
    service = LLMScanModeService(settings=settings, storage_path=path)

    result = service.update(LLMScanMode.HIGH)

    preset = llm_scan_mode_preset(LLMScanMode.HIGH)
    assert result.mode is LLMScanMode.HIGH
    assert result.runtime_override_active is True
    # Services keep a reference to the cached Settings instance, so the change
    # must be visible without rebuilding it.
    assert settings.llm_scan_mode is LLMScanMode.HIGH
    assert settings.llm_cycle_request_budget == preset.llm_cycle_request_budget
    assert settings.llm_daily_request_budget == preset.llm_daily_request_budget
    assert settings.llm_cycle_token_budget == preset.llm_cycle_token_budget
    assert settings.llm_daily_token_budget == preset.llm_daily_token_budget

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["llm_scan_mode"] == "high"
    assert stored["llm_cycle_request_budget"] == preset.llm_cycle_request_budget
    assert load_runtime_llm_overrides(path) == stored


def test_switching_back_to_savings_restores_its_budgets(tmp_path: Path) -> None:
    service = LLMScanModeService(
        settings=Settings(_env_file=None),
        storage_path=tmp_path / "llm.json",
    )

    service.update(LLMScanMode.HIGH)
    result = service.update(LLMScanMode.SAVINGS)

    savings = llm_scan_mode_preset(LLMScanMode.SAVINGS)
    assert result.mode is LLMScanMode.SAVINGS
    assert result.effective_budgets["llm_cycle_request_budget"] == savings.llm_cycle_request_budget
    assert result.effective_budgets["llm_daily_token_budget"] == savings.llm_daily_token_budget


def test_llm_loader_ignores_trading_keys_and_vice_versa(tmp_path: Path) -> None:
    path = tmp_path / "llm.json"
    path.write_text(
        json.dumps(
            {
                "llm_scan_mode": "normal",
                "llm_cycle_request_budget": 24,
                "execution_mode": "real",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_runtime_llm_overrides(path)

    assert "execution_mode" not in loaded
    assert loaded["llm_scan_mode"] == "normal"
    assert loaded["llm_cycle_request_budget"] == 24


def test_stored_mode_uses_the_current_preset_budgets(tmp_path: Path) -> None:
    # A savings mode saved when the preset capped the day at 18 requests must
    # pick up the retuned preset instead of staying pinned to the old cap.
    path = tmp_path / "llm.json"
    path.write_text(
        json.dumps({"llm_scan_mode": "savings", "llm_daily_request_budget": 18}),
        encoding="utf-8",
    )

    loaded = load_runtime_llm_overrides(path)

    preset = llm_scan_mode_preset(LLMScanMode.SAVINGS)
    assert loaded["llm_daily_request_budget"] == preset.llm_daily_request_budget
    # A full day at the default 30-minute interval: 48 analyses x 2 calls.
    assert preset.llm_daily_request_budget >= 48 * preset.estimated_llm_calls_per_analysis


def test_corrupt_llm_config_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "llm.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert load_runtime_llm_overrides(path) == {}


@pytest.mark.parametrize("mode", list(LLMScanMode))
def test_every_preset_round_trips_through_the_service(mode: LLMScanMode, tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    service = LLMScanModeService(settings=settings, storage_path=tmp_path / "llm.json")

    service.update(mode)

    assert service.snapshot().mode is mode
    assert settings.llm_scan_mode is mode
