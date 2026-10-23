"""
Security — API Key encryption manager.

API keys are NEVER stored in plain text.
All exchange API keys are encrypted with Fernet (symmetric encryption)
before persisting to the database.

In production, the encryption key itself should be stored in a secret manager
(HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager).

Usage:
    manager = KeyManager()
    encrypted = manager.encrypt("my-api-key")
    original = manager.decrypt(encrypted)
"""

from __future__ import annotations

import hashlib

from cryptography.fernet import Fernet, InvalidToken

from src.shared.config.settings import get_settings
from src.shared.exceptions.domain import SecurityError
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class KeyManager:
    """
    Handles encryption and decryption of sensitive credentials.

    Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
    The encryption key is sourced from API_KEY_ENCRYPTION_KEY env var.
    """

    def __init__(self) -> None:
        settings = get_settings()
        raw_key = settings.api_key_encryption_key

        if not raw_key:
            # Development fallback — generate a deterministic key from APP_SECRET_KEY
            # In production this MUST be set explicitly
            logger.warning(
                "api_key_encryption_key_not_set",
                message="Using derived key from APP_SECRET_KEY. "
                "Set API_KEY_ENCRYPTION_KEY explicitly in production.",
            )
            derived = hashlib.sha256(settings.app_secret_key.encode()).digest()
            import base64

            raw_key = base64.urlsafe_b64encode(derived).decode()

        try:
            self._fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
        except ValueError as exc:
            msg = (
                "Invalid API_KEY_ENCRYPTION_KEY. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
            raise SecurityError(msg) from exc

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext API key.

        Returns:
            URL-safe base64 encoded encrypted string
        """
        if not plaintext:
            return ""
        encrypted_bytes = self._fernet.encrypt(plaintext.encode("utf-8"))
        return encrypted_bytes.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt an encrypted API key.

        Raises:
            SecurityError: If decryption fails (wrong key or tampered data)
        """
        if not ciphertext:
            return ""
        try:
            decrypted_bytes = self._fernet.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except InvalidToken as exc:
            logger.error("api_key_decryption_failed", error="Invalid token or wrong key")
            msg = (
                "Failed to decrypt API key. The key may be corrupted or the encryption key changed."
            )
            raise SecurityError(msg) from exc

    def is_encrypted(self, value: str) -> bool:
        """
        Heuristically check if a value looks like it's already encrypted.

        Fernet tokens start with 'gAAAAA' in base64.
        """
        return value.startswith("gAAAAA") and len(value) > 50

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet encryption key."""
        return Fernet.generate_key().decode()
