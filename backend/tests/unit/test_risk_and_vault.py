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

    # Testnet and mainnet credentials coexist without replacing each other.
    mainnet_cred = vault.store_credential(
        exchange="binance",
        api_key="mainnetKey123456",
        api_secret="mainnetSecret123456",
        is_testnet=False,
    )
    assert mainnet_cred.is_testnet is False
    assert len(vault.list_configured_exchanges()) == 2
    assert vault.get_credential_summary("binance", environment="testnet") == cred
    assert vault.get_credential_summary("binance", environment="mainnet") == mainnet_cred
    assert vault.list_environments("binance") == ["mainnet", "testnet"]

    vault.set_active_environment("mainnet")
    assert vault.get_decrypted_credential("binance")[0] == "mainnetKey123456"

    # Test delete
    assert vault.delete_credential("binance", environment="testnet") is True
    assert vault.get_decrypted_credential("binance", environment="testnet") is None
    assert vault.get_decrypted_credential("binance", environment="mainnet") is not None
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
def test_position_sizer_respects_remaining_asset_exposure():
    req = PositionSizeRequest(
        symbol="BTC/USDT",
        account_balance=10000.0,
        entry_price=60000.0,
        stop_loss_price=58000.0,
        risk_percentage=1.5,
        current_symbol_exposure_usd=1500.0,
    )

    res = PositionSizer.calculate(req)

    # Per-asset cap is 20% ($2000); $1500 is already used, so only $500 remain.
    assert res.is_valid_setup is True
    assert res.recommended_notional_usd == 500.0
    assert round(res.recommended_units, 6) == 0.008333
    assert "remaining exposure capacity" in res.notes


@pytest.mark.unit
def test_position_sizer_respects_remaining_portfolio_exposure():
    req = PositionSizeRequest(
        symbol="BTC/USDT",
        account_balance=10000.0,
        entry_price=60000.0,
        stop_loss_price=58000.0,
        risk_percentage=1.5,
        current_total_exposure_usd=9700.0,
    )

    res = PositionSizer.calculate(req)

    # Portfolio cap is 100% ($10000); only $300 remain even though the
    # per-asset cap would allow $2000.
    assert res.recommended_notional_usd == 300.0
    assert round(res.recommended_units, 6) == 0.005
    assert "remaining exposure capacity" in res.notes


@pytest.mark.unit
def test_position_sizer_invalidates_when_no_exposure_capacity_remains():
    req = PositionSizeRequest(
        symbol="BTC/USDT",
        account_balance=10000.0,
        entry_price=60000.0,
        stop_loss_price=58000.0,
        risk_percentage=1.5,
        current_total_exposure_usd=10000.0,
    )

    res = PositionSizer.calculate(req)

    assert res.is_valid_setup is False
    assert res.recommended_units == 0.0
    assert "No remaining exposure capacity" in res.notes


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


@pytest.mark.unit
def test_drawdown_guard_rejects_buy_above_remaining_asset_allocation():
    guard = DrawdownGuard(profile=RiskProfile(max_position_size_pct=10.0))
    request = OrderRiskCheckRequest(
        symbol="BTC/USDT",
        side="BUY",
        price=100.0,
        quantity=2.0,
        stop_loss=98.0,
        take_profit=104.0,
        account_balance=1000.0,
        current_symbol_exposure_usd=90.0,
    )

    result = guard.validate_order(request)

    assert not result.is_allowed
    assert any("per-asset allocation" in reason for reason in result.violation_reasons)


@pytest.mark.unit
def test_drawdown_guard_rejects_buy_above_portfolio_exposure_cap():
    guard = DrawdownGuard(profile=RiskProfile(max_portfolio_exposure_pct=50.0))
    request = OrderRiskCheckRequest(
        symbol="ETH/USDT",
        side="BUY",
        price=100.0,
        quantity=4.0,
        stop_loss=98.0,
        take_profit=104.0,
        account_balance=1000.0,
        current_total_exposure_usd=480.0,
    )

    result = guard.validate_order(request)

    assert not result.is_allowed
    assert any("portfolio exposure capacity" in reason for reason in result.violation_reasons)


@pytest.mark.unit
def test_drawdown_guard_does_not_block_sell_for_exposure_capacity():
    guard = DrawdownGuard(profile=RiskProfile(max_position_size_pct=10.0))
    request = OrderRiskCheckRequest(
        symbol="BTC/USDT",
        side="SELL",
        price=100.0,
        quantity=5.0,
        stop_loss=100.1,
        account_balance=1000.0,
        current_total_exposure_usd=500.0,
        current_symbol_exposure_usd=500.0,
    )

    result = guard.validate_order(request)

    assert result.is_allowed
    assert result.max_allowed_quantity > 0


@pytest.mark.unit
def test_risk_service_does_not_overwrite_more_conservative_request_state():
    risk_service = RiskService()
    request = OrderRiskCheckRequest(
        symbol="BTC/USDT",
        side="BUY",
        price=100.0,
        quantity=0.1,
        stop_loss=98.0,
        take_profit=104.0,
        account_balance=1000.0,
        current_drawdown_pct=risk_service.guard.profile.max_drawdown_pct,
    )

    result = risk_service.validate_order(request)

    assert not result.is_allowed
    assert risk_service.guard.kill_switch_active
    assert any("drawdown" in reason.lower() for reason in result.violation_reasons)
