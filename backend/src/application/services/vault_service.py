"""
VaultService — Application Layer for Encrypted Credential Management and Live Connection Testing.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.domain.market.entities import Exchange
from src.infrastructure.exchange.live_exchange import LiveExchangeAdapter
from src.infrastructure.security.credential_vault import CredentialVault, EncryptedCredential
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class ConnectionTestResult(BaseModel):
    exchange: str
    status: str
    is_testnet: bool
    latency_ms: float
    balances: dict[str, float]


class VaultService:
    """
    Coordinates encryption, storage, and live health verification of exchange API keys.
    """

    def __init__(self, vault: CredentialVault | None = None) -> None:
        self.vault = vault or CredentialVault()

    def store_credentials(
        self,
        exchange: str,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
        is_testnet: bool = True,
    ) -> EncryptedCredential:
        return self.vault.store_credential(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
        )

    def list_credentials(self) -> list[EncryptedCredential]:
        return self.vault.list_configured_exchanges()

    def delete_credential(self, exchange: str) -> bool:
        return self.vault.delete_credential(exchange)

    async def test_connection(self, exchange: str = "binance") -> ConnectionTestResult:
        creds = self.vault.get_decrypted_credential(exchange)
        if not creds:
            raise ValueError(
                f"No credentials configured for {exchange}. Please save API keys first."
            )

        api_key, secret, passphrase = creds
        summary = self.vault.get_credential_summary(exchange)
        is_testnet = summary.is_testnet if summary else True

        try:
            exchange_id = Exchange(exchange.lower())
        except ValueError as exc:
            raise ValueError(f"Unsupported exchange: {exchange}") from exc

        adapter = LiveExchangeAdapter(
            exchange=exchange_id,
            api_key=api_key,
            api_secret=secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
        )

        try:
            ping_res = await adapter.ping()
            balances = {}
            try:
                balances = await adapter.fetch_balances()
            except Exception as b_exc:
                logger.warning("balance_fetch_notice", detail=str(b_exc))

            return ConnectionTestResult(
                exchange=exchange,
                status="connected",
                is_testnet=is_testnet,
                latency_ms=ping_res.get("latency_ms", 0.0),
                balances=balances,
            )
        finally:
            await adapter.close()
