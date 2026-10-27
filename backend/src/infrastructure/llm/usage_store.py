"""Durable LLM usage counters and provider cooldowns.

The gateway enforces daily token/request budgets and parks providers after a
quota or rate-limit failure. Keeping that state only in memory meant a backend
restart, or rebuilding the gateway after a settings change in the UI, reset the
daily budget and retried providers that were known to be exhausted.

The state is small and written once per LLM call, so a JSON file next to the
other runtime overrides is enough; it does not depend on Redis being up.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

# backend/src/infrastructure/llm/usage_store.py -> backend/vault
_DEFAULT_STATE_FILE = Path(__file__).resolve().parents[3] / "vault" / ".llm_usage_state.json"


class LLMUsageStore(Protocol):
    def load(self) -> dict[str, Any] | None: ...

    def save(self, state: dict[str, Any]) -> None: ...


class InMemoryLLMUsageStore:
    """Process-local store, used by tests and as a fallback."""

    def __init__(self) -> None:
        self._state: dict[str, Any] | None = None

    def load(self) -> dict[str, Any] | None:
        return dict(self._state) if self._state is not None else None

    def save(self, state: dict[str, Any]) -> None:
        self._state = dict(state)


class FileLLMUsageStore:
    """Atomically written JSON file holding the gateway's usage state."""

    def __init__(self, path: Path | None = None) -> None:
        configured = os.getenv("LLM_USAGE_STATE_PATH", "").strip()
        self._path = path or (Path(configured) if configured else _DEFAULT_STATE_FILE)

    def load(self) -> dict[str, Any] | None:
        try:
            if not self._path.exists():
                return None
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("llm_usage_state_read_failed", error=str(exc))
            return None
        return raw if isinstance(raw, dict) else None

    def save(self, state: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(f"{self._path.suffix}.tmp")
            temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(temp, self._path)
        except Exception as exc:
            # Losing the persisted counters is not worth failing an analysis.
            logger.warning("llm_usage_state_write_failed", error=str(exc))


def default_usage_store() -> LLMUsageStore:
    """File store in production; in-memory under pytest unless a path is set."""
    if "pytest" in sys.modules and not os.getenv("LLM_USAGE_STATE_PATH", "").strip():
        return InMemoryLLMUsageStore()
    return FileLLMUsageStore()
