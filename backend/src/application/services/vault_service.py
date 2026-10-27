"""
VaultService — Application Layer for Encrypted Credential Management and Live Connection Testing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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
    # Key restrictions as reported by the exchange; None when it cannot say
    # (Binance Testnet has no endpoint for them).
    permissions_checked: bool = False
    can_trade: bool | None = None
    withdrawals_enabled: bool | None = None
    ip_restricted: bool | None = None
    warnings: list[str] = Field(default_factory=list)


def _permission_warnings(restrictions: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if restrictions.get("enableWithdrawals"):
        warnings.append(
            "La clave permite RETIRADAS. Desactívalo en Binance: Mainnet quedará bloqueado "
            "mientras siga activo."
        )
    if not restrictions.get("ipRestrict"):
        warnings.append(
            "La clave no está limitada por IP. Recomendado: restringirla a la IP de este equipo."
        )
    if not restrictions.get("enableSpotAndMarginTrading"):
        warnings.append("La clave no tiene permiso de trading spot: el bot no podrá operar.")
    return warnings


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

    def set_active_environment(self, environment: str) -> None:
        self.vault.set_active_environment(environment)

    def delete_credential(self, exchange: str, is_testnet: bool | None = None) -> bool:
        environment = None
        if is_testnet is not None:
            environment = "testnet" if is_testnet else "mainnet"
        return self.vault.delete_credential(exchange, environment=environment)

    async def test_connection(
        self, exchange: str = "binance", is_testnet: bool | None = None
    ) -> ConnectionTestResult:
        environment = None
        if is_testnet is not None:
            environment = "testnet" if is_testnet else "mainnet"
        creds = self.vault.get_decrypted_credential(exchange, environment=environment)
        if not creds:
            env_label = f" ({environment})" if environment else ""
            raise ValueError(
                f"No credentials configured for {exchange}{env_label}. Please save API keys first."
            )

        api_key, secret, passphrase = creds
        summary = self.vault.get_credential_summary(exchange, environment=environment)
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

            restrictions: dict[str, Any] | None = None
            warnings: list[str] = []
            try:
                restrictions = await adapter.fetch_api_restrictions()
            except Exception as r_exc:
                warnings.append(f"No se pudieron leer los permisos de la clave: {r_exc}")

            env_name = "testnet" if is_testnet else "mainnet"
            if restrictions is not None:
                warnings.extend(_permission_warnings(restrictions))
                self.vault.record_verification(
                    exchange,
                    env_name,
                    can_trade=bool(restrictions.get("enableSpotAndMarginTrading")),
                    has_withdrawal_permission=bool(restrictions.get("enableWithdrawals")),
                    ip_restricted=bool(restrictions.get("ipRestrict")),
                    permissions_checked=True,
                )
            else:
                self.vault.record_verification(exchange, env_name)
            logger.info(
                "credential_connection_verified",
                exchange=exchange,
                environment=env_name,
                permissions_checked=restrictions is not None,
                warnings=len(warnings),
            )

            return ConnectionTestResult(
                exchange=exchange,
                status="connected",
                is_testnet=is_testnet,
                latency_ms=ping_res.get("latency_ms", 0.0),
                balances=balances,
                permissions_checked=restrictions is not None,
                can_trade=(
                    bool(restrictions.get("enableSpotAndMarginTrading")) if restrictions else None
                ),
                withdrawals_enabled=(
                    bool(restrictions.get("enableWithdrawals")) if restrictions else None
                ),
                ip_restricted=bool(restrictions.get("ipRestrict")) if restrictions else None,
                warnings=warnings,
            )
        finally:
            await adapter.close()
