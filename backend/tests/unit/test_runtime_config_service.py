"""Runtime execution settings are validated and persisted safely."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from src.application.services.runtime_config_service import (
    RuntimeConfigService,
    RuntimeTradingConfigUpdate,
)
from src.shared.config.settings import Settings
from src.shared.exceptions.domain import SecurityError, ValidationError

if TYPE_CHECKING:
    from pathlib import Path


class FakeVaultService:
    def __init__(self) -> None:
        self.environment = "testnet"
        self.vault = self

    def set_active_environment(self, environment: str) -> None:
        self.environment = environment

    def get_credential_summary(self, exchange: str, environment: str) -> object | None:
        if environment == "testnet":
            return object()
        return None


def test_runtime_config_persists_and_applies_overrides(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    vault = FakeVaultService()
    path = tmp_path / "runtime.json"
    service = RuntimeConfigService(settings=settings, vault_service=vault, storage_path=path)

    result = service.update(
        RuntimeTradingConfigUpdate(
            execution_mode="real",
            enable_real_trading=True,
            real_trading_max_notional=25,
            real_trading_allowed_symbols=[" btc-usdt ", "BTC/USDT"],
            confirm=True,
        )
    )

    assert result.real_trading_enabled is True
    assert result.real_trading_max_notional == 25
    assert result.real_trading_allowed_symbols == ["BTC/USDT"]
    assert settings.real_trading_enabled is True
    assert json.loads(path.read_text(encoding="utf-8"))["execution_mode"] == "real"
    assert vault.environment == "testnet"


def test_real_mode_requires_explicit_confirmation(tmp_path: Path) -> None:
    service = RuntimeConfigService(
        settings=Settings(_env_file=None),
        vault_service=FakeVaultService(),
        storage_path=tmp_path / "runtime.json",
    )

    with pytest.raises(SecurityError, match="confirmar explícitamente"):
        service.update(RuntimeTradingConfigUpdate(execution_mode="real", enable_real_trading=True))


def test_mainnet_requires_credentials_and_separate_opt_in(tmp_path: Path) -> None:
    service = RuntimeConfigService(
        settings=Settings(_env_file=None),
        vault_service=FakeVaultService(),
        storage_path=tmp_path / "runtime.json",
    )

    with pytest.raises(ValidationError, match="No hay credenciales mainnet"):
        service.update(
            RuntimeTradingConfigUpdate(
                execution_mode="real",
                enable_real_trading=True,
                trading_environment="mainnet",
                allow_mainnet_trading=True,
                confirm=True,
            )
        )


def test_mainnet_opt_in_is_available_when_env_mode_is_real_but_env_is_wrong(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        execution_mode="real",
        enable_real_trading=True,
    )

    class MainnetOnlyVault(FakeVaultService):
        def get_credential_summary(self, exchange: str, environment: str) -> object | None:
            return object() if environment == "mainnet" else None

    service = RuntimeConfigService(
        settings=settings,
        vault_service=MainnetOnlyVault(),
        storage_path=tmp_path / "runtime.json",
    )

    result = service.update(RuntimeTradingConfigUpdate(allow_mainnet_trading=True, confirm=True))

    assert result.allow_mainnet_trading is True
    assert result.trading_environment.value == "testnet"


def test_manual_confirmation_cannot_be_disabled_for_real_mode(tmp_path: Path) -> None:
    service = RuntimeConfigService(
        settings=Settings(_env_file=None),
        vault_service=FakeVaultService(),
        storage_path=tmp_path / "runtime.json",
    )

    with pytest.raises(ValidationError, match="confirmación manual es obligatoria"):
        service.update(
            RuntimeTradingConfigUpdate(
                execution_mode="real",
                enable_real_trading=True,
                require_manual_confirmation=False,
                confirm=True,
            )
        )


def test_runtime_snapshot_never_exposes_credentials(tmp_path: Path) -> None:
    service = RuntimeConfigService(
        settings=Settings(_env_file=None),
        vault_service=FakeVaultService(),
        storage_path=tmp_path / "runtime.json",
    )

    snapshot = service.snapshot().model_dump()
    assert "api_key" not in snapshot
    assert "api_secret" not in snapshot
    assert snapshot["credentials"] == {"testnet": True, "mainnet": False}
