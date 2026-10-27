"""
Secure Credential Vault — Encrypted In-Memory / File Persistent Key Store.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from src.infrastructure.security.key_manager import KeyManager
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

# Default vault location is resolved relative to THIS file (backend/.vault_store.json)
# so it does not depend on the current working directory. Can be overridden with the
# VAULT_STORAGE_PATH environment variable (useful for tests, Docker volumes, etc.).
# Layout: backend/src/infrastructure/security/credential_vault.py -> parents[3] == backend/
_DEFAULT_VAULT_FILE = Path(__file__).resolve().parents[3] / ".vault_store.json"

# Credentials are stored independently per exchange environment so the user can
# keep testnet and mainnet keys side by side and switch from the UI.
_VALID_ENVIRONMENTS = ("testnet", "mainnet")


class EncryptedCredential(BaseModel):
    exchange: str
    encrypted_api_key: str
    encrypted_secret: str
    encrypted_passphrase: str | None = None
    masked_key: str
    is_testnet: bool = True
    can_trade: bool = False
    has_withdrawal_permission: bool = False
    last_verified_at: datetime | None = None
    # Set only after the exchange itself reported the key's restrictions
    # (Binance GET /sapi/v1/account/apiRestrictions). None means unverified.
    permissions_verified_at: datetime | None = None
    ip_restricted: bool | None = None
    status: str = "configured"


class CredentialVault:
    """
    Encrypts and persists API credentials using Fernet AES encryption.
    """

    def __init__(
        self, key_manager: KeyManager | None = None, storage_path: Path | None = None
    ) -> None:
        self.km = key_manager or KeyManager()
        self.storage_file = storage_path or Path(
            os.getenv("VAULT_STORAGE_PATH", str(_DEFAULT_VAULT_FILE))
        )
        self._credentials: dict[str, EncryptedCredential] = {}
        self.active_environment = "testnet"
        self._load()

    @staticmethod
    def _normalize_environment(environment: str | None) -> str:
        candidate = (environment or "").strip().lower()
        if candidate not in _VALID_ENVIRONMENTS:
            raise ValueError(f"Invalid exchange environment: {environment!r}")
        return candidate

    @staticmethod
    def _environment_for(is_testnet: bool) -> str:
        return "testnet" if is_testnet else "mainnet"

    @staticmethod
    def _credential_key(exchange: str, environment: str) -> str:
        return f"{exchange.strip().lower()}:{environment}"

    def set_active_environment(self, environment: str) -> None:
        """Select which credential set is used when callers omit an environment."""
        self.active_environment = self._normalize_environment(environment)

    def _load(self) -> None:
        if self.storage_file.exists():
            try:
                data = json.loads(self.storage_file.read_text(encoding="utf-8"))
                migrated = False
                for raw_key, value in data.items():
                    cred = EncryptedCredential(**value)
                    key = str(raw_key).strip().lower()
                    if ":" not in key:
                        # Backwards compatibility: the old vault stored a single
                        # credential per exchange. Migrate it to an explicit env.
                        key = self._credential_key(key, self._environment_for(cred.is_testnet))
                        migrated = True
                    self._credentials[key] = cred
                if migrated:
                    self._save()
            except Exception as exc:
                logger.warning("vault_load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            dump = {k: v.model_dump(mode="json") for k, v in self._credentials.items()}
            self.storage_file.write_text(json.dumps(dump, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("vault_save_failed", error=str(exc))

    def store_credential(
        self,
        exchange: str,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
        is_testnet: bool = True,
    ) -> EncryptedCredential:
        masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) >= 8 else "****"
        environment = self._environment_for(is_testnet)

        cred = EncryptedCredential(
            exchange=exchange.lower(),
            encrypted_api_key=self.km.encrypt(api_key),
            encrypted_secret=self.km.encrypt(api_secret),
            encrypted_passphrase=self.km.encrypt(passphrase) if passphrase else None,
            masked_key=masked,
            is_testnet=is_testnet,
            can_trade=True,
            has_withdrawal_permission=False,
            # Saving is not verifying: these are only confirmed by a live
            # connection test (record_verification).
            last_verified_at=None,
            status="active",
        )

        self._credentials[self._credential_key(exchange, environment)] = cred
        self._save()
        logger.info(
            "credential_stored_securely",
            exchange=exchange.lower(),
            environment=environment,
            masked=masked,
        )
        return cred

    def record_verification(
        self,
        exchange: str,
        environment: str,
        *,
        can_trade: bool | None = None,
        has_withdrawal_permission: bool | None = None,
        ip_restricted: bool | None = None,
        permissions_checked: bool = False,
    ) -> EncryptedCredential | None:
        """Store what a live connection test confirmed about a key."""
        key = self._credential_key(exchange, self._normalize_environment(environment))
        cred = self._credentials.get(key)
        if cred is None:
            return None
        now = datetime.now(tz=UTC)
        cred.last_verified_at = now
        if permissions_checked:
            cred.permissions_verified_at = now
            if can_trade is not None:
                cred.can_trade = can_trade
            if has_withdrawal_permission is not None:
                cred.has_withdrawal_permission = has_withdrawal_permission
            cred.ip_restricted = ip_restricted
        self._save()
        return cred

    def get_decrypted_credential(
        self, exchange: str, environment: str | None = None
    ) -> tuple[str, str, str | None] | None:
        cred = self.get_credential_summary(exchange, environment=environment)
        if not cred:
            return None
        api_key = self.km.decrypt(cred.encrypted_api_key)
        secret = self.km.decrypt(cred.encrypted_secret)
        passphrase = (
            self.km.decrypt(cred.encrypted_passphrase) if cred.encrypted_passphrase else None
        )
        return api_key, secret, passphrase

    def get_credential_summary(
        self, exchange: str, environment: str | None = None
    ) -> EncryptedCredential | None:
        selected = self._normalize_environment(environment or self.active_environment)
        return self._credentials.get(self._credential_key(exchange, selected))

    def list_configured_exchanges(self) -> list[EncryptedCredential]:
        return sorted(
            self._credentials.values(),
            key=lambda cred: (cred.exchange, cred.is_testnet),
        )

    def list_environments(self, exchange: str) -> list[str]:
        prefix = f"{exchange.strip().lower()}:"
        return sorted(
            key.removeprefix(prefix) for key in self._credentials if key.startswith(prefix)
        )

    def delete_credential(self, exchange: str, environment: str | None = None) -> bool:
        if environment is not None:
            key = self._credential_key(exchange, self._normalize_environment(environment))
            if key not in self._credentials:
                return False
            del self._credentials[key]
            self._save()
            return True

        prefix = f"{exchange.strip().lower()}:"
        keys = [key for key in self._credentials if key.startswith(prefix)]
        if not keys:
            return False
        for key in keys:
            del self._credentials[key]
        self._save()
        return True
