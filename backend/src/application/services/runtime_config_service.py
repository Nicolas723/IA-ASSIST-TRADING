"""
Runtime trading configuration managed by the UI.

The .env file remains the safe default, but operators can update the trading
interlocks from the frontend. Accepted overrides are validated, persisted to an
atomic JSON file next to the encrypted vault, and applied to the live Settings
instance so every service sees the change immediately.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from src.shared.config.settings import (
    RUNTIME_TRADING_FIELDS,
    ExecutionMode,
    Settings,
    TradingEnvironment,
    apply_runtime_overrides,
    get_settings,
    runtime_config_path,
)
from src.shared.exceptions.domain import SecurityError, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from src.application.services.vault_service import VaultService


class RuntimeTradingConfig(BaseModel):
    """Editable trading settings. These are safe to persist and expose."""

    execution_mode: ExecutionMode
    trading_environment: TradingEnvironment
    enable_real_trading: bool
    allow_mainnet_trading: bool
    require_manual_confirmation: bool
    real_trading_max_notional: Decimal = Field(gt=Decimal("0"))
    real_trading_allowed_symbols: list[str] = Field(default_factory=list)
    real_trading_oco_enabled: bool = False
    enable_autonomous_real_trading: bool = False

    @field_validator("real_trading_allowed_symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        return _normalize_symbols(value)

    @classmethod
    def from_settings(cls, settings: Settings) -> RuntimeTradingConfig:
        return cls(
            execution_mode=settings.execution_mode,
            trading_environment=settings.trading_environment,
            enable_real_trading=settings.enable_real_trading,
            allow_mainnet_trading=settings.allow_mainnet_trading,
            require_manual_confirmation=settings.require_manual_confirmation,
            real_trading_max_notional=settings.real_trading_max_notional,
            real_trading_allowed_symbols=list(settings.real_trading_allowed_symbols),
            real_trading_oco_enabled=settings.real_trading_oco_enabled,
            enable_autonomous_real_trading=settings.enable_autonomous_real_trading,
        )

    @property
    def real_trading_enabled(self) -> bool:
        return self.enable_real_trading and self.execution_mode == ExecutionMode.REAL

    @property
    def mainnet_trading_enabled(self) -> bool:
        return (
            self.real_trading_enabled
            and self.trading_environment == TradingEnvironment.MAINNET
            and self.allow_mainnet_trading
        )


class RuntimeTradingConfigUpdate(BaseModel):
    """Partial update accepted by the UI. ``confirm`` gates dangerous changes."""

    execution_mode: ExecutionMode | None = None
    trading_environment: TradingEnvironment | None = None
    enable_real_trading: bool | None = None
    allow_mainnet_trading: bool | None = None
    require_manual_confirmation: bool | None = None
    real_trading_max_notional: Decimal | None = Field(default=None, gt=Decimal("0"))
    real_trading_allowed_symbols: list[str] | None = None
    real_trading_oco_enabled: bool | None = None
    enable_autonomous_real_trading: bool | None = None
    confirm: bool = False

    @field_validator("real_trading_allowed_symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_symbols(value) if value is not None else None


class RuntimeTradingConfigResponse(RuntimeTradingConfig):
    """Effective config plus live credential/readiness information."""

    real_trading_active: bool
    mainnet_trading_active: bool
    credentials: dict[str, bool] = Field(default_factory=dict)
    runtime_override_active: bool = False


def _normalize_symbols(value: list[str] | None) -> list[str]:
    symbols: list[str] = []
    for raw in value or []:
        symbol = raw.strip().upper().replace("-", "/").replace("_", "/")
        if not symbol:
            continue
        if "/" not in symbol:
            raise ValueError(f"Símbolo inválido: {raw!r}. Usa formato BASE/QUOTE.")
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


class RuntimeConfigService:
    """Validate, persist and apply trading settings changed from the UI."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vault_service: VaultService | None = None,
        storage_path: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vault_service = vault_service
        self.storage_path = storage_path or runtime_config_path()

    def current(self) -> RuntimeTradingConfig:
        return RuntimeTradingConfig.from_settings(self.settings)

    def snapshot(self) -> RuntimeTradingConfigResponse:
        config = self.current()
        return RuntimeTradingConfigResponse(
            **config.model_dump(),
            real_trading_active=config.real_trading_enabled,
            mainnet_trading_active=config.mainnet_trading_enabled,
            credentials=self._credential_status(),
            runtime_override_active=self.storage_path.exists(),
        )

    def update(self, payload: RuntimeTradingConfigUpdate) -> RuntimeTradingConfig:
        before = self.current()
        updates = payload.model_dump(exclude_unset=True, exclude={"confirm"})
        merged = {**before.model_dump(), **updates}
        candidate = RuntimeTradingConfig.model_validate(merged)

        self._validate(before=before, candidate=candidate, confirm=payload.confirm)

        persisted = candidate.model_dump(mode="json")
        self._persist({key: persisted[key] for key in RUNTIME_TRADING_FIELDS})
        apply_runtime_overrides(self.settings, candidate.model_dump())

        setter = getattr(self.vault_service, "set_active_environment", None)
        if callable(setter):
            setter(candidate.trading_environment.value)

        return self.current()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _credential_status(self) -> dict[str, bool]:
        if self.vault_service is None:
            return {}
        exchange = self.settings.real_trading_exchange
        statuses: dict[str, bool] = {}
        for environment in TradingEnvironment:
            try:
                summary = self.vault_service.vault.get_credential_summary(
                    exchange, environment=environment.value
                )
            except Exception:
                summary = None
            statuses[environment.value] = summary is not None
        return statuses

    def _has_credentials(self, environment: TradingEnvironment) -> bool:
        if self.vault_service is None:
            return True
        exchange = self.settings.real_trading_exchange
        try:
            summary = self.vault_service.vault.get_credential_summary(
                exchange, environment=environment.value
            )
        except Exception:
            return False
        return summary is not None

    def _validate(
        self,
        *,
        before: RuntimeTradingConfig,
        candidate: RuntimeTradingConfig,
        confirm: bool,
    ) -> None:
        if candidate.real_trading_enabled and not candidate.require_manual_confirmation:
            raise ValidationError(
                "La confirmación manual es obligatoria mientras el trading real esté activo."
            )

        requires_active_credentials = candidate.real_trading_enabled and (
            not before.real_trading_enabled
            or candidate.trading_environment != before.trading_environment
        )
        if requires_active_credentials and not self._has_credentials(candidate.trading_environment):
            raise ValidationError(
                f"No hay credenciales {candidate.trading_environment.value} configuradas. "
                "Guárdalas antes de activar el modo real."
            )

        if candidate.real_trading_enabled and not before.real_trading_enabled and not confirm:
            raise SecurityError("Debes confirmar explícitamente la activación del modo real.")

        activating_mainnet_real = (
            candidate.real_trading_enabled
            and candidate.trading_environment == TradingEnvironment.MAINNET
            and not before.mainnet_trading_enabled
        )
        if activating_mainnet_real and not candidate.allow_mainnet_trading:
            raise SecurityError("Mainnet requiere la segunda confirmación (ALLOW_MAINNET_TRADING).")

        if activating_mainnet_real and not confirm:
            raise SecurityError("Debes confirmar explícitamente el uso de MAINNET con dinero real.")

        enabled_mainnet_permission = (
            candidate.allow_mainnet_trading and not before.allow_mainnet_trading
        )
        if enabled_mainnet_permission and not confirm:
            raise SecurityError("Debes confirmar explícitamente la habilitación de MAINNET.")

        if candidate.enable_autonomous_real_trading:
            if not candidate.real_trading_enabled:
                raise ValidationError(
                    "El bot autónomo real requiere Modo Real + trading real habilitado."
                )
            if not self._has_credentials(candidate.trading_environment):
                raise ValidationError(
                    f"No hay credenciales {candidate.trading_environment.value} para operar en real."
                )
            if (
                candidate.trading_environment == TradingEnvironment.MAINNET
                and not candidate.allow_mainnet_trading
            ):
                raise SecurityError("El bot autónomo en MAINNET requiere la segunda confirmación.")

    def _persist(self, overrides: dict[str, Any]) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.tmp")
            temp_path.write_text(
                json.dumps(overrides, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise ValidationError(
                f"No se pudo persistir la configuración de trading: {exc}"
            ) from exc
