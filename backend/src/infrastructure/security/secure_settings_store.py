"""Encrypted store for UI-managed integration settings.

Exchange credentials already live in ``CredentialVault``, keyed by
exchange/environment. LLM provider keys, the Telegram bot token and the
heartbeat URL do not fit that model, so they are kept in a separate file that is
encrypted as a single Fernet blob with the same ``API_KEY_ENCRYPTION_KEY``.

Keeping the whole payload encrypted means a copied file does not reveal which
providers are configured, the chat id or the ping URL. The file is written
atomically so a crash cannot leave a truncated store.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from src.infrastructure.security.key_manager import KeyManager
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

# backend/src/infrastructure/security/secure_settings_store.py -> backend/vault
_DEFAULT_STORAGE_FILE = Path(__file__).resolve().parents[3] / "vault" / ".secure_settings.json"

_SECTIONS = ("llm", "telegram", "heartbeat")


class SecureSettingsStore:
    """Persist non-exchange integration settings encrypted at rest."""

    def __init__(
        self,
        key_manager: KeyManager | None = None,
        storage_path: Path | None = None,
    ) -> None:
        self._km = key_manager or KeyManager()
        configured = os.getenv("SECURE_SETTINGS_PATH", "").strip()
        self.storage_file = storage_path or (
            Path(configured) if configured else _DEFAULT_STORAGE_FILE
        )
        self._data: dict[str, Any] = {}
        self._load()

    @staticmethod
    def _pytest_guard() -> bool:
        """Ignore the developer's local store during the test suite."""
        return "pytest" in sys.modules and not os.getenv("SECURE_SETTINGS_PATH", "").strip()

    def _load(self) -> None:
        if self._pytest_guard() or not self.storage_file.exists():
            return
        try:
            raw = json.loads(self.storage_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("secure_settings_read_failed", error=str(exc))
            return
        if not isinstance(raw, dict):
            logger.warning("secure_settings_invalid_payload", path=str(self.storage_file))
            return
        ciphertext = str(raw.get("ciphertext") or "").strip()
        if not ciphertext:
            return
        try:
            plaintext = self._km.decrypt(ciphertext)
            payload = json.loads(plaintext) if plaintext else {}
        except Exception as exc:
            logger.warning("secure_settings_decrypt_failed", error=str(exc))
            return
        if isinstance(payload, dict):
            self._data = payload

    def _save(self) -> None:
        if self._pytest_guard():
            # Never overwrite the developer's real store from the test suite.
            return
        plaintext = json.dumps(self._data, ensure_ascii=False)
        document = {"version": 1, "ciphertext": self._km.encrypt(plaintext)}
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.storage_file.with_suffix(f"{self.storage_file.suffix}.tmp")
        temp_file.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_file, self.storage_file)

    def section(self, name: str) -> dict[str, Any]:
        if name not in _SECTIONS:
            raise ValueError(f"Unknown secure settings section: {name!r}")
        value = self._data.get(name)
        return dict(value) if isinstance(value, dict) else {}

    def set_section(self, name: str, values: dict[str, Any]) -> None:
        if name not in _SECTIONS:
            raise ValueError(f"Unknown secure settings section: {name!r}")
        self._data[name] = values
        self._save()

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow copy for applying at startup (never log this)."""
        return {name: self.section(name) for name in _SECTIONS}
