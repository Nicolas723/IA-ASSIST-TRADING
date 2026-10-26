"""Runtime LLM consumption mode selected from the UI.

Three presets trade market breadth for cost: savings analyzes one market per
committee run, normal up to three in sequence, and high analyzes the whole scan
universe in parallel. The mode is persisted next to the credential vault and
applied to the live Settings instance, so the next autonomous cycle uses it
without a restart.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.shared.config.settings import (
    LLM_SCAN_MODE_PRESETS,
    RUNTIME_LLM_FIELDS,
    LLMScanMode,
    LLMScanModePreset,
    Settings,
    apply_llm_runtime_overrides,
    get_settings,
    runtime_llm_config_path,
)
from src.shared.exceptions.domain import ValidationError

if TYPE_CHECKING:
    from pathlib import Path


class LLMScanModePresetResponse(BaseModel):
    """One selectable mode and the caps it applies."""

    mode: LLMScanMode
    label: str
    description: str
    max_markets: int
    parallel: bool
    estimated_llm_calls_per_analysis: int
    llm_cycle_request_budget: int
    llm_daily_request_budget: int
    llm_cycle_token_budget: int
    llm_daily_token_budget: int


class LLMScanModeResponse(BaseModel):
    """Current mode, available presets and the budgets in force."""

    mode: LLMScanMode
    active: LLMScanModePresetResponse
    presets: list[LLMScanModePresetResponse]
    effective_budgets: dict[str, int] = Field(default_factory=dict)
    runtime_override_active: bool = False
    note: str = (
        "El modo cambia cuántos mercados analiza el comité y si lo hace en paralelo. "
        "Los presupuestos son topes duros, no un gasto objetivo; las tarifas reales "
        "dependen de tu proveedor y modelo."
    )


class LLMScanModeUpdate(BaseModel):
    """Payload accepted by the mode selector."""

    mode: LLMScanMode


def _preset_response(mode: LLMScanMode, preset: LLMScanModePreset) -> LLMScanModePresetResponse:
    return LLMScanModePresetResponse(
        mode=mode,
        label=preset.label,
        description=preset.description,
        max_markets=preset.max_markets,
        parallel=preset.parallel,
        estimated_llm_calls_per_analysis=preset.estimated_llm_calls_per_analysis,
        llm_cycle_request_budget=preset.llm_cycle_request_budget,
        llm_daily_request_budget=preset.llm_daily_request_budget,
        llm_cycle_token_budget=preset.llm_cycle_token_budget,
        llm_daily_token_budget=preset.llm_daily_token_budget,
    )


class LLMScanModeService:
    """Read, validate and persist the LLM consumption mode."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        storage_path: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.storage_path = storage_path or runtime_llm_config_path()

    def snapshot(self) -> LLMScanModeResponse:
        mode = self.settings.llm_scan_mode
        preset = LLM_SCAN_MODE_PRESETS[mode]
        return LLMScanModeResponse(
            mode=mode,
            active=_preset_response(mode, preset),
            presets=[
                _preset_response(candidate, candidate_preset)
                for candidate, candidate_preset in LLM_SCAN_MODE_PRESETS.items()
            ],
            effective_budgets={
                "llm_cycle_request_budget": int(self.settings.llm_cycle_request_budget),
                "llm_daily_request_budget": int(self.settings.llm_daily_request_budget),
                "llm_cycle_token_budget": int(self.settings.llm_cycle_token_budget),
                "llm_daily_token_budget": int(self.settings.llm_daily_token_budget),
            },
            runtime_override_active=self.storage_path.exists(),
        )

    def update(self, mode: LLMScanMode) -> LLMScanModeResponse:
        preset = LLM_SCAN_MODE_PRESETS[mode]
        overrides: dict[str, Any] = {
            "llm_scan_mode": mode.value,
            "llm_cycle_request_budget": preset.llm_cycle_request_budget,
            "llm_daily_request_budget": preset.llm_daily_request_budget,
            "llm_cycle_token_budget": preset.llm_cycle_token_budget,
            "llm_daily_token_budget": preset.llm_daily_token_budget,
        }
        self._persist(overrides)
        apply_llm_runtime_overrides(self.settings, overrides)
        return self.snapshot()

    def _persist(self, overrides: dict[str, Any]) -> None:
        filtered = {key: value for key, value in overrides.items() if key in RUNTIME_LLM_FIELDS}
        existing: dict[str, Any] = {}
        if self.storage_path.exists():
            try:
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = {
                        key: value for key, value in raw.items() if key in RUNTIME_LLM_FIELDS
                    }
            except Exception:
                existing = {}
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.tmp")
            temp_path.write_text(
                json.dumps({**existing, **filtered}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise ValidationError(f"No se pudo persistir el modo de consumo IA: {exc}") from exc
