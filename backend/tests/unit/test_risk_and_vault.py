"""
Unit tests for CredentialVault, DrawdownGuard, PositionSizer, and RiskService.
"""

from pathlib import Path

import pytest

from src.application.services.risk_service import RiskService
from src.domain.risk.drawdown_guard import DrawdownGuard
from src.domain.risk.entities import (
    CircuitBreakerState,
    OrderRiskCheckRequest,
    PositionSizeRequest,
    RiskProfile,
)
from src.domain.risk.position_sizer import PositionSizer
from src.infrastructure.security.credential_vault import CredentialVault
from src.infrastructure.security.key_manager import KeyManager


@pytest.mark.unit
def test_credential_vault_encryption(tmp_path: Path):
    store_file = tmp_path / "vault_test.json"
    km = KeyManager()
    vault = CredentialVault(key_manager=km, storage_path=store_file)

    cred = vault.store_credential(
        exchange="binance",
        api_key="apiKey1234567890",
        api_secret="secretKeyABCDEF123456",
        is_testnet=True,
    )

    assert cred.exchange == "binance"
    assert cred.masked_key == "apiK...7890"
    assert cred.encrypted_api_key != "apiKey1234567890"
    assert cred.is_testnet is True

    # Test decryption
    decrypted = vault.get_decrypted_credential("binance")
    assert decrypted is not None
    api_k, sec_k, pass_k = decrypted
    assert api_k == "apiKey1234567890"
    assert sec_k == "secretKeyABCDEF123456"
    assert pass_k is None

    # Test list
    all_creds = vault.list_configured_exchanges()
    assert len(all_creds) == 1

    # Test delete
    assert vault.delete_credential("binance") is True
    assert vault.get_decrypted_credential("binance") is None


@pytest.mark.unit
def test_drawdown_guard_circuit_breaker():
    guard = DrawdownGuard(profile=RiskProfile(max_daily_loss_pct=5.0, max_drawdown_pct=15.0))

    # Initial state
    assert guard.state == CircuitBreakerState.ARMED
    assert guard.kill_switch_active is False

    # Manual trip
    guard.trip("Emergency drawdown limit breached")
    assert guard.state == CircuitBreakerState.TRIPPED
    assert guard.kill_switch_active is True

    # Reset
    guard.reset()
    assert guard.state == CircuitBreakerState.ARMED
    assert guard.kill_switch_active is False


@pytest.mark.unit
def test_position_sizer_fixed_fractional():
    req = PositionSizeRequest(
        symbol="BTC/USDT",
        account_balance=10000.0,
        entry_price=60000.0,
        stop_loss_price=58000.0,  # $2000 per BTC risk
        risk_percentage=1.5,
    )
    res = PositionSizer.calculate(req)
    assert res.is_valid_setup is True
    assert res.risk_amount_usd == 150.0
    # $2000 max allocation / $60,000 = 0.033 BTC
    assert round(res.recommended_units, 3) == 0.033


@pytest.mark.unit
def test_risk_service_order_validation():
    risk_service = RiskService()

    # Valid order ($325 position <= $500 max 5% limit)
    valid_req = OrderRiskCheckRequest(
        symbol="BTC/USDT",
        side="BUY",
        order_type="MARKET",
        price=65000.0,
        quantity=0.005,
        stop_loss=63500.0,
        take_profit=68000.0,
        account_balance=10000.0,
        current_open_positions_count=1,
        current_daily_loss_pct=1.0,
        current_drawdown_pct=2.0,
    )
    result = risk_service.validate_order(valid_req)
    assert result.is_allowed is True
    assert len(result.violation_reasons) == 0
