"""Credential Vault API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.application.services.vault_service import ConnectionTestResult, VaultService
from src.infrastructure.security.credential_vault import EncryptedCredential
from src.interfaces.api.v1.deps import get_vault_service

router = APIRouter()


class StoreKeyBody(BaseModel):
    exchange: str = "binance"
    api_key: str = Field(..., min_length=8)
    api_secret: str = Field(..., min_length=8)
    passphrase: str | None = None
    is_testnet: bool = True


class TestConnectionBody(BaseModel):
    exchange: str = "binance"
    is_testnet: bool | None = None


@router.get(
    "/status", response_model=list[EncryptedCredential], summary="List configured credential status"
)
async def get_vault_status(
    vault_service: VaultService = Depends(get_vault_service),
) -> list[EncryptedCredential]:
    """Retrieve masked credential summaries and verification dates."""
    return vault_service.list_credentials()


@router.post("/keys", response_model=EncryptedCredential, summary="Save encrypted API credentials")
async def store_keys(
    body: StoreKeyBody,
    vault_service: VaultService = Depends(get_vault_service),
) -> EncryptedCredential:
    """Encrypt and store exchange API keys with AES-256."""
    try:
        return vault_service.store_credentials(
            exchange=body.exchange,
            api_key=body.api_key,
            api_secret=body.api_secret,
            passphrase=body.passphrase,
            is_testnet=body.is_testnet,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Failed to encrypt credentials: {exc}"
        ) from exc


@router.post(
    "/test-connection",
    response_model=ConnectionTestResult,
    summary="Test live exchange connectivity",
)
async def test_live_connection(
    body: TestConnectionBody,
    vault_service: VaultService = Depends(get_vault_service),
) -> ConnectionTestResult:
    """Decrypts credentials in-memory, executes ping, and tests balance querying."""
    try:
        return await vault_service.test_connection(
            exchange=body.exchange, is_testnet=body.is_testnet
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Exchange connection test failed: {exc}"
        ) from exc


@router.delete("/keys/{exchange}", summary="Delete exchange credentials")
async def delete_keys(
    exchange: str,
    is_testnet: bool | None = Query(default=None),
    vault_service: VaultService = Depends(get_vault_service),
) -> dict[str, str]:
    deleted = vault_service.delete_credential(exchange, is_testnet=is_testnet)
    if not deleted:
        label = (
            exchange
            if is_testnet is None
            else (f"{exchange} ({'testnet' if is_testnet else 'mainnet'})")
        )
        raise HTTPException(status_code=404, detail=f"No credentials found for {label}")
    return {"message": f"Credentials for {exchange} wiped from vault."}
