"""Adaptive committee weights, durable LLM usage state, batch prices and CSRF guard."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.application.services.account_snapshot_service import AccountSnapshotService
from src.application.services.adaptive_weights import compute_adaptive_weights
from src.domain.agents.coordinator import DEFAULT_AGENT_WEIGHTS, AgentCoordinator
from src.domain.analytics.entities import AgentPerformance
from src.infrastructure.llm.gateway import LLMGateway, LLMRequest, LLMResponse
from src.infrastructure.llm.usage_store import InMemoryLLMUsageStore
from src.shared.config.settings import LLMProvider
from src.shared.exceptions.domain import LLMQuotaError

# ---------------------------------------------------------------------------
# Adaptive weights
# ---------------------------------------------------------------------------


def _perf(agent: str, correct: int, incorrect: int) -> AgentPerformance:
    return AgentPerformance(
        agent_type=agent,
        decisions_scored=correct + incorrect,
        correct=correct,
        incorrect=incorrect,
    )


def test_adaptive_weights_keep_defaults_with_few_samples() -> None:
    report = compute_adaptive_weights(
        [_perf("technical_analyst", 5, 1)], enabled=True, min_samples=20
    )
    assert report.applied is False
    assert report.weights == DEFAULT_AGENT_WEIGHTS


def test_adaptive_weights_reward_accuracy_and_stay_bounded() -> None:
    report = compute_adaptive_weights(
        [_perf("technical_analyst", 80, 20), _perf("price_action_analyst", 20, 80)],
        enabled=True,
        min_samples=20,
    )
    assert report.applied is True
    assert sum(report.weights.values()) == pytest.approx(1.0, abs=1e-3)
    assert report.weights["technical_analyst"] > DEFAULT_AGENT_WEIGHTS["technical_analyst"]
    assert report.weights["price_action_analyst"] < DEFAULT_AGENT_WEIGHTS["price_action_analyst"]
    for agent in report.agents:
        assert 0.5 <= agent.multiplier <= 1.5
    risk = next(a for a in report.agents if a.agent_type == "risk_manager")
    assert risk.adjusted is False


def test_adaptive_weights_preview_does_not_apply_when_disabled() -> None:
    report = compute_adaptive_weights(
        [_perf("technical_analyst", 80, 20)], enabled=False, min_samples=20
    )
    assert report.applied is False
    assert report.weights == DEFAULT_AGENT_WEIGHTS
    technical = next(a for a in report.agents if a.agent_type == "technical_analyst")
    assert technical.adjusted is True


def test_coordinator_uses_injected_weights() -> None:
    coordinator = AgentCoordinator()
    coordinator.set_agent_weights({"technical_analyst": 0.6})
    assert coordinator.agent_weights["technical_analyst"] == 0.6
    assert coordinator.agent_weights["risk_manager"] == DEFAULT_AGENT_WEIGHTS["risk_manager"]


# ---------------------------------------------------------------------------
# Durable LLM usage
# ---------------------------------------------------------------------------


class _Adapter:
    def __init__(self, provider: LLMProvider, error: Exception | None = None) -> None:
        self._provider = provider
        self._error = error

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self._error is not None:
            raise self._error
        return LLMResponse(
            content="ok",
            model="m",
            provider=self._provider,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

    async def health_check(self) -> bool:
        return True


async def test_gateway_usage_and_cooldowns_survive_rebuild() -> None:
    store = InMemoryLLMUsageStore()
    failing = _Adapter(LLMProvider.GROQ, LLMQuotaError("no balance", provider="groq"))
    working = _Adapter(LLMProvider.GOOGLE)
    gateway = LLMGateway(
        {LLMProvider.GROQ: failing, LLMProvider.GOOGLE: working},  # type: ignore[dict-item]
        usage_store=store,
    )

    await gateway.complete(
        LLMRequest(messages=[], model=""),
        provider=LLMProvider.GROQ,
        fallback_providers=[LLMProvider.GOOGLE],
    )
    before = gateway.usage_status()

    rebuilt = LLMGateway(
        {LLMProvider.GROQ: failing, LLMProvider.GOOGLE: working},  # type: ignore[dict-item]
        usage_store=store,
    )
    after = rebuilt.usage_status()

    assert after["tokens_used"] == before["tokens_used"] == 15
    assert after["request_attempts"] == 2
    assert [item["provider"] for item in rebuilt.provider_cooldowns] == ["groq"]


# ---------------------------------------------------------------------------
# Batch pricing and the baseline guard
# ---------------------------------------------------------------------------


class _Live:
    def __init__(self) -> None:
        self.details = {
            "USDT": {"total": 100.0, "free": 100.0, "used": 0.0},
            "BTC": {"total": 1.0, "free": 1.0, "used": 0.0},
        }

    async def account_details(self, _exchange: Any = None) -> dict[str, dict[str, float]]:
        return self.details

    def status(self, _exchange: Any = None) -> Any:
        return SimpleNamespace(exchange="binance", testnet=True)

    def get_portfolio(self, _mode: Any) -> Any:
        return SimpleNamespace(total_realized_pnl=Decimal("0"), positions=[])


async def _never(_symbol: str) -> float | None:
    raise AssertionError("single lookup must not be used when a batch lookup exists")


async def test_batch_pricing_uses_one_call_and_skips_baseline_when_degraded() -> None:
    calls: list[list[str]] = []
    fail = True

    async def batch(symbols: list[str]) -> dict[str, float]:
        calls.append(symbols)
        if fail:
            raise RuntimeError("exchange down")
        return {"BTC/USDT": 1_000.0}

    service = AccountSnapshotService(
        live_service=_Live(), price_lookup=_never, batch_price_lookup=batch
    )

    degraded = await service.snapshot()
    assert degraded.equity_usdt == pytest.approx(100.0)

    fail = False
    healthy = await service.snapshot()
    # The degraded reading was not persisted as the reference.
    assert healthy.baseline_equity == pytest.approx(1_100.0)
    assert healthy.change_usdt == pytest.approx(0.0)
    assert calls == [["BTC/USDT"], ["BTC/USDT"]]


async def test_simple_earn_balances_are_valued_like_their_base_asset() -> None:
    live = _Live()
    live.details = {
        "LDUSDT": {"total": 3.0, "free": 3.0, "used": 0.0},
        "LDBTC": {"total": 0.001, "free": 0.001, "used": 0.0},
    }
    asked: list[list[str]] = []

    async def batch(symbols: list[str]) -> dict[str, float]:
        asked.append(symbols)
        return {"BTC/USDT": 1_000.0}

    service = AccountSnapshotService(
        live_service=live, price_lookup=_never, batch_price_lookup=batch
    )
    snapshot = await service.snapshot()

    assert snapshot.equity_usdt == pytest.approx(4.0)
    assert snapshot.unpriced_assets == []
    assert asked == [["BTC/USDT"]]


# ---------------------------------------------------------------------------
# Mainnet key safety gate
# ---------------------------------------------------------------------------


def _runtime_with_mainnet_key(tmp_path: Any, summary: object) -> Any:
    from src.application.services.runtime_config_service import RuntimeConfigService
    from src.shared.config.settings import Settings

    class _Vault:
        def get_credential_summary(self, exchange: str, environment: str) -> object | None:
            return summary

        def set_active_environment(self, environment: str) -> None:
            return None

    settings = Settings()
    settings.execution_mode = "real"  # type: ignore[assignment]
    settings.enable_real_trading = True
    return RuntimeConfigService(
        settings=settings,
        vault_service=SimpleNamespace(vault=_Vault()),  # type: ignore[arg-type]
        storage_path=tmp_path / "runtime.json",
    )


@pytest.mark.parametrize(
    ("verified", "withdrawals", "message"),
    [
        (None, False, "Probar conexión"),
        ("2026-10-26T00:00:00Z", True, "permite retiradas"),
    ],
)
def test_mainnet_activation_requires_a_verified_key_without_withdrawals(
    tmp_path: Any, verified: str | None, withdrawals: bool, message: str
) -> None:
    from src.application.services.runtime_config_service import RuntimeTradingConfigUpdate
    from src.shared.exceptions.domain import SecurityError

    summary = SimpleNamespace(
        permissions_verified_at=verified, has_withdrawal_permission=withdrawals
    )
    service = _runtime_with_mainnet_key(tmp_path, summary)
    with pytest.raises(SecurityError, match=message):
        service.update(
            RuntimeTradingConfigUpdate(
                trading_environment="mainnet", allow_mainnet_trading=True, confirm=True
            )
        )


def test_mainnet_activation_passes_with_a_cleared_key(tmp_path: Any) -> None:
    from src.application.services.runtime_config_service import RuntimeTradingConfigUpdate

    summary = SimpleNamespace(
        permissions_verified_at="2026-10-26T00:00:00Z", has_withdrawal_permission=False
    )
    service = _runtime_with_mainnet_key(tmp_path, summary)
    result = service.update(
        RuntimeTradingConfigUpdate(
            trading_environment="mainnet", allow_mainnet_trading=True, confirm=True
        )
    )
    assert result.trading_environment.value == "mainnet"


def test_vault_records_verified_permissions(tmp_path: Any) -> None:
    from src.infrastructure.security.credential_vault import CredentialVault

    vault = CredentialVault(storage_path=tmp_path / "vault.json")
    stored = vault.store_credential(
        "binance", "apiKey1234567890", "secret1234567890", is_testnet=False
    )
    assert stored.permissions_verified_at is None
    assert stored.last_verified_at is None

    updated = vault.record_verification(
        "binance",
        "mainnet",
        can_trade=True,
        has_withdrawal_permission=False,
        ip_restricted=True,
        permissions_checked=True,
    )
    assert updated is not None
    assert updated.permissions_verified_at is not None
    assert updated.ip_restricted is True


async def test_markets_load_after_clock_sync() -> None:
    """Binance mainnet signs its first load_markets call; the clock must be synced first."""
    from src.domain.market.entities import Exchange
    from src.infrastructure.exchange.live_exchange import LiveExchangeAdapter

    calls: list[str] = []

    class _Client:
        def __init__(self) -> None:
            self.markets: dict[str, Any] = {}
            self.options = {"adjustForTimeDifference": True}

        async def load_time_difference(self) -> None:
            calls.append("clock")

        async def load_markets(self) -> None:
            calls.append("markets")

    adapter = LiveExchangeAdapter(
        exchange=Exchange.BINANCE, api_key="k" * 16, api_secret="s" * 16, is_testnet=False
    )
    await adapter._ensure_markets(_Client())  # type: ignore[arg-type]
    await adapter.close()
    assert calls == ["clock", "markets"]


async def test_api_restrictions_are_skipped_on_testnet() -> None:
    from src.domain.market.entities import Exchange
    from src.infrastructure.exchange.live_exchange import LiveExchangeAdapter

    adapter = LiveExchangeAdapter(
        exchange=Exchange.BINANCE, api_key="k" * 16, api_secret="s" * 16, is_testnet=True
    )
    assert await adapter.fetch_api_restrictions() is None
    await adapter.close()


# ---------------------------------------------------------------------------
# Cross-site guard
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from src.main import create_app

    return TestClient(create_app())


def test_cross_site_post_is_blocked(client: TestClient) -> None:
    response = client.post(
        "/api/v1/settings/heartbeat/test", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_trusted_origin_and_originless_posts_pass_the_guard(client: TestClient) -> None:
    trusted = client.post(
        "/api/v1/settings/heartbeat/test", headers={"Origin": "http://localhost:5173"}
    )
    originless = client.post("/api/v1/settings/heartbeat/test")
    assert trusted.status_code != 403
    assert originless.status_code != 403
