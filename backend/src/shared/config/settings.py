"""
Application settings — Pydantic BaseSettings.

All configuration comes from environment variables or .env file.
Grouped by domain for clarity and type safety.

Design decisions:
- Pydantic v2 BaseSettings for strict validation at startup
- Nested models for grouping (DatabaseSettings, RedisSettings, etc.)
- Computed properties for derived values (e.g., full DATABASE_URL)
- Fail-fast: invalid configuration raises an error before the app starts
- No hardcoded defaults for secrets — they must be explicitly provided
"""

from __future__ import annotations

import json
import logging
import os
import sys
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from pydantic import Field, PostgresDsn, RedisDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the .env path relative to THIS file (backend/.env) so configuration is
# found regardless of the current working directory or where the project lives.
# Layout: backend/src/shared/config/settings.py  ->  parents[3] == backend/
_ENV_FILE_PATH = Path(__file__).resolve().parents[3] / ".env"

# Runtime overrides are persisted next to the encrypted credential vault so they
# can be changed from the UI without editing .env or restarting the backend.
_DEFAULT_RUNTIME_CONFIG_PATH = _ENV_FILE_PATH.parent / "vault" / ".runtime_config.json"

# Only these fields are allowed to be overridden from the UI. Keeping this list
# explicit prevents accidental persistence of secrets or unrelated settings.
RUNTIME_TRADING_FIELDS = (
    "execution_mode",
    "enable_real_trading",
    "require_manual_confirmation",
    "real_trading_max_notional",
    "real_trading_allowed_symbols",
    "allow_mainnet_trading",
    "enable_autonomous_real_trading",
    "real_trading_oco_enabled",
    "trading_environment",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    PAPER = "paper"
    PRODUCTION = "production"


class ExecutionMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    REAL = "real"


class TradingEnvironment(StrEnum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LLMProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"


# ---------------------------------------------------------------------------
# Nested Settings Groups
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATABASE_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    url: PostgresDsn = Field(
        default=cast(
            "PostgresDsn",
            "postgresql+asyncpg://trading_user:password@localhost:5432/trading_db",
        )
    )
    pool_size: int = Field(default=10, ge=1, le=50)
    max_overflow: int = Field(default=20, ge=0, le=100)
    pool_timeout: int = Field(default=30, ge=1)
    echo: bool = Field(default=False)


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    url: RedisDsn = Field(default=cast("RedisDsn", "redis://localhost:6379/0"))
    max_connections: int = Field(default=50, ge=1)
    password: str | None = None


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_key: str = Field(default="")
    default_model: str = Field(default="gpt-4o-mini")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANTHROPIC_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_key: str = Field(default="")
    default_model: str = Field(default="claude-opus-4-5")
    max_tokens: int = Field(default=8192)


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GOOGLE_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_key: str = Field(default="")
    default_model: str = Field(default="gemini-3.8-flash")


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OLLAMA_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    base_url: str = Field(default="http://localhost:11434")
    default_model: str = Field(default="llama3.2")
    embedding_model: str = Field(default="nomic-embed-text")


class OpenRouterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENROUTER_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_key: str = Field(default="")
    base_url: str = Field(default="https://openrouter.ai/api/v1")
    default_model: str = Field(default="openai/gpt-4o-mini")


class DeepSeekSettings(BaseSettings):
    """DeepSeek exposes an OpenAI-compatible chat completions API."""

    model_config = SettingsConfigDict(
        env_prefix="DEEPSEEK_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_key: str = Field(default="")
    base_url: str = Field(default="https://api.deepseek.com")
    default_model: str = Field(default="deepseek-chat")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAX_",
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    daily_loss_pct: Decimal = Field(default=Decimal("2.0"), gt=Decimal("0"))
    position_size_pct: Decimal = Field(default=Decimal("5.0"), gt=Decimal("0"))
    portfolio_risk_pct: Decimal = Field(default=Decimal("20.0"), gt=Decimal("0"))
    open_positions: int = Field(default=10, ge=1)
    max_drawdown_pct: Decimal = Field(default=Decimal("10.0"), gt=Decimal("0"))
    max_risk_per_trade_pct: Decimal = Field(default=Decimal("1.5"), gt=Decimal("0"))
    portfolio_exposure_pct: Decimal = Field(
        default=Decimal("100.0"), gt=Decimal("0"), le=Decimal("100")
    )
    max_open_positions: int = Field(default=5, ge=1)


class AgentLLMConfig(BaseSettings):
    """Deprecated per-agent routing placeholders (unused by the runtime).

    The committee routes every agent through the global primary/fallback chain
    in ``LLMGateway``. These fields are kept only so old deployments that still
    define ``AGENT_*_PROVIDER`` variables do not fail validation; they have no
    effect on routing.
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    technical_analyst_provider: LLMProvider = LLMProvider.OPENAI
    technical_analyst_model: str = "gpt-4o"
    price_action_provider: LLMProvider = LLMProvider.OPENAI
    price_action_model: str = "gpt-4o"
    sentiment_provider: LLMProvider = LLMProvider.GOOGLE
    sentiment_model: str = "gemini-3.8-flash"
    macro_provider: LLMProvider = LLMProvider.ANTHROPIC
    macro_model: str = "claude-opus-4-5"
    fundamental_provider: LLMProvider = LLMProvider.ANTHROPIC
    fundamental_model: str = "claude-opus-4-5"
    risk_manager_provider: LLMProvider = LLMProvider.OPENAI
    risk_manager_model: str = "gpt-4o"
    strategy_optimizer_provider: LLMProvider = LLMProvider.OPENAI
    strategy_optimizer_model: str = "o3-mini"
    supervisor_provider: LLMProvider = LLMProvider.OLLAMA
    supervisor_model: str = "llama3.2"
    performance_coach_provider: LLMProvider = LLMProvider.ANTHROPIC
    performance_coach_model: str = "claude-opus-4-5"


# ---------------------------------------------------------------------------
# Main Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """
    Central application configuration.

    Reads from environment variables and .env file.
    Validation happens at startup — the app will not start with invalid config.
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -------------------------------------------------------------------------
    # Core application
    # -------------------------------------------------------------------------
    app_name: str = Field(default="AI Trading Assistant")
    app_env: AppEnv = Field(default=AppEnv.DEVELOPMENT)
    app_version: str = Field(default="0.1.0")
    app_debug: bool = Field(default=False)
    app_secret_key: str = Field(default="insecure-dev-key-change-in-production")
    app_allowed_hosts: list[str] = Field(default=["localhost", "127.0.0.1"])
    app_cors_origins: list[str] = Field(default=["http://localhost:3000", "http://localhost:5173"])

    # -------------------------------------------------------------------------
    # Server
    # -------------------------------------------------------------------------
    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=8000, ge=1024, le=65535)
    server_workers: int = Field(default=1, ge=1)

    # -------------------------------------------------------------------------
    # Nested configuration groups
    # -------------------------------------------------------------------------
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    agent_llm: AgentLLMConfig = Field(default_factory=AgentLLMConfig)
    llm_primary_provider: LLMProvider = Field(default=LLMProvider.ANTHROPIC)
    llm_fallback_providers: list[LLMProvider] = Field(
        default_factory=lambda: [
            LLMProvider.OPENAI,
            LLMProvider.GOOGLE,
            LLMProvider.DEEPSEEK,
        ]
    )
    llm_fallback_enabled: bool = Field(default=True)
    # Optional per-process daily token cap. 0 disables the cap. The counter is
    # in-memory and resets at UTC midnight or on restart.
    llm_daily_token_budget: int = Field(default=0, ge=0)
    # Optional per-process daily request cap. 0 disables the cap. Counts every
    # provider attempt (including failures), because free tiers usually limit
    # requests per day rather than tokens.
    llm_daily_request_budget: int = Field(default=0, ge=0)
    # Optional per-cycle token cap. 0 disables the cap. The counter resets when
    # the autonomous engine starts a new analysis cycle, which bounds the cost
    # of a single committee run even when the daily budget is generous.
    llm_cycle_token_budget: int = Field(default=0, ge=0)
    # Optional per-cycle request cap. 0 disables the cap.
    llm_cycle_request_budget: int = Field(default=0, ge=0)
    # Optional cost estimate. Rates are USD per one million tokens and default
    # to 0 ("unknown") because provider pricing changes and is model-specific.
    # The gateway only reports an estimate when at least one rate is set.
    llm_input_cost_per_million_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    llm_output_cost_per_million_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    # Hard cap for a single provider call. Some SDKs have no usable timeout and
    # a hung request would block the autonomous cycle indefinitely. When the
    # cap fires, the gateway cancels the call and moves to the next provider.
    llm_request_timeout_seconds: int = Field(default=60, ge=5, le=600)

    # Correlation-aware sizing for new long exposure. Spot automation is
    # long-only, so only positive correlation with an existing position is
    # penalised: a negative correlation diversifies instead of concentrating.
    # The guard compares simple returns of closed candles and only acts when at
    # least `correlation_min_samples` timestamps overlap. Set
    # CORRELATION_GUARD_ENABLED=false to keep the previous behaviour.
    correlation_guard_enabled: bool = Field(default=True)
    correlation_reduce_threshold: float = Field(default=0.75, gt=0, le=1)
    correlation_block_threshold: float = Field(default=0.95, gt=0, le=1)
    correlation_min_samples: int = Field(default=30, ge=10)
    correlation_lookback: int = Field(default=200, ge=30)

    # -------------------------------------------------------------------------
    # Trading
    # -------------------------------------------------------------------------
    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER)
    trading_environment: TradingEnvironment = Field(default=TradingEnvironment.TESTNET)
    enable_real_trading: bool = Field(default=False)
    require_manual_confirmation: bool = Field(default=True)
    paper_trading_initial_balance: Decimal = Field(default=Decimal("10000.0"))
    paper_trading_currency: str = Field(default="USDT")

    # Real execution safety interlocks
    real_trading_exchange: str = Field(default="binance")
    real_trading_max_notional: Decimal = Field(default=Decimal("1000.0"), gt=Decimal("0"))
    real_trading_allowed_symbols: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT"]
    )
    allow_mainnet_trading: bool = Field(default=False)
    enable_autonomous_real_trading: bool = Field(default=False)
    real_trading_guard_interval_seconds: int = Field(default=15, ge=5, le=300)
    real_trading_reconcile_interval_seconds: int = Field(default=300, ge=30, le=3600)
    # Lookback used to recover orders that filled while the process was down.
    # Bounded so a restart never replays the entire exchange order history.
    real_trading_order_recovery_lookback_seconds: int = Field(default=86400, ge=300, le=604800)
    real_trading_oco_enabled: bool = Field(default=False)
    real_trading_oco_stop_limit_buffer_pct: Decimal = Field(
        default=Decimal("0.1"), ge=Decimal("0"), le=Decimal("5")
    )

    # Bounded background shadow data collection (public market data only).
    shadow_scan_autorun: bool = Field(default=False)
    shadow_scan_symbols: list[str] = Field(
        default_factory=lambda: [
            "BTC/USDT",
            "ETH/USDT",
            "BNB/USDT",
            "SOL/USDT",
            "XRP/USDT",
        ],
        min_length=1,
        max_length=5,
    )
    # One scan per 1h evaluation horizon, limiting mechanical overlap.
    shadow_scan_interval_seconds: int = Field(default=3600, ge=3600, le=86400)
    # Conservative round-trip estimate (fees + slippage) used only to describe
    # shadow returns net of trading costs. It is not a realised P&L figure.
    shadow_round_trip_cost_pct: Decimal = Field(
        default=Decimal("0.25"), ge=Decimal("0"), le=Decimal("5")
    )
    # Read-only opportunity gate thresholds. These never route orders; they only
    # decide whether a shadow candidate may be surfaced for manual review.
    opportunity_min_net_edge_pct: Decimal = Field(
        default=Decimal("0.10"), ge=Decimal("0"), le=Decimal("10")
    )
    opportunity_max_quoted_spread_pct: Decimal = Field(
        default=Decimal("0.10"), ge=Decimal("0"), le=Decimal("5")
    )
    opportunity_max_scan_age_seconds: int = Field(default=900, ge=0, le=86_400)
    opportunity_min_validated_evaluations: int = Field(default=30, ge=1, le=10_000)

    # -------------------------------------------------------------------------
    # Notifications / alerts
    # -------------------------------------------------------------------------
    alerts_enabled: bool = Field(default=False)
    alert_min_severity: str = Field(default="info")
    alert_timeout_seconds: float = Field(default=5.0, gt=0)
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    alert_webhook_url: str = Field(default="")
    email_smtp_host: str = Field(default="")
    email_smtp_port: int = Field(default=587, ge=1, le=65535)
    email_username: str = Field(default="")
    email_password: str = Field(default="")
    email_from: str = Field(default="")
    email_to: str = Field(default="")

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    api_key_encryption_key: str = Field(default="")
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=60)
    jwt_refresh_token_expire_days: int = Field(default=30)
    rate_limit_requests: int = Field(default=100)
    rate_limit_window_seconds: int = Field(default=60)

    # -------------------------------------------------------------------------
    # Feature flags
    # -------------------------------------------------------------------------
    enable_sentiment_analysis: bool = Field(default=True)
    enable_fundamental_analysis: bool = Field(default=False)
    enable_macro_analysis: bool = Field(default=True)
    enable_ai_agents: bool = Field(default=True)
    enable_backtesting: bool = Field(default=True)
    enable_paper_trading: bool = Field(default=True)

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    @field_validator("log_level", mode="before", check_fields=False)
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """Accept case-insensitive log level strings (e.g. "INFO")."""
        return v.lower() if isinstance(v, str) else v

    @field_validator("log_format", mode="before", check_fields=False)
    @classmethod
    def _validate_log_format(cls, v: str) -> str:
        """Accept case-insensitive log format strings (e.g. "JSON")."""
        return v.lower() if isinstance(v, str) else v

    log_level: LogLevel = Field(default=LogLevel.INFO)
    log_format: LogFormat = Field(default=LogFormat.JSON)
    sentry_dsn: str | None = Field(default=None)
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    metrics_enabled: bool = Field(default=False)  # Enable Prometheus metrics endpoint
    metrics_port: int = Field(default=9090)
    otel_enabled: bool = Field(default=False)
    otel_service_name: str = Field(default="ai-trading-assistant")
    otel_exporter_otlp_endpoint: str = Field(default="http://localhost:4317")

    # -------------------------------------------------------------------------
    # Data ingestion
    # -------------------------------------------------------------------------
    data_historical_days: int = Field(default=365, ge=1)
    data_candle_timeframes: list[str] = Field(default=["1m", "5m", "15m", "1h", "4h", "1d"])
    default_symbols: list[str] = Field(default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    # Public market-data source. Execution stays Binance-only and independently gated.
    market_data_exchange: str = Field(default="binance", pattern="^(binance|bybit)$")
    cache_ttl_seconds: int = Field(default=300)

    # -------------------------------------------------------------------------
    # Research memory
    # -------------------------------------------------------------------------
    # Opt-in: research notes reach LLM prompts only when enabled, only when
    # ``supported`` and current, and always inside a guarded data block.
    research_context_in_prompts: bool = Field(default=False)
    research_context_max_notes: int = Field(default=5, ge=1, le=20)
    research_context_max_chars: int = Field(default=1200, ge=200, le=8000)

    # -------------------------------------------------------------------------
    # Computed properties
    # -------------------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_development(self) -> bool:
        return self.app_env == AppEnv.DEVELOPMENT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def real_trading_enabled(self) -> bool:
        """Real trading requires BOTH the flag AND the mode to be set explicitly."""
        return self.enable_real_trading and self.execution_mode == ExecutionMode.REAL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mainnet_trading_enabled(self) -> bool:
        """Mainnet (real-money) orders need a second explicit opt-in."""
        return self.real_trading_enabled and self.allow_mainnet_trading

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("app_secret_key")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if v == "insecure-dev-key-change-in-production":
            import warnings

            warnings.warn(
                "Using insecure default APP_SECRET_KEY. Set a proper key in production.",
                stacklevel=2,
            )
        return v

    @field_validator("execution_mode")
    @classmethod
    def validate_execution_mode(cls, v: ExecutionMode, _info: Any) -> ExecutionMode:
        # This validator is intentionally permissive here;
        # real trading auth is checked at the service layer
        return v

    @field_validator("shadow_scan_symbols")
    @classmethod
    def validate_shadow_scan_symbols(cls, values: list[str]) -> list[str]:
        symbols = [item.strip().upper().replace("-", "/").replace("_", "/") for item in values]
        if any("/" not in item for item in symbols):
            raise ValueError("Shadow scan symbols must use BASE/QUOTE notation.")
        if len(set(symbols)) != len(symbols):
            raise ValueError("Shadow scan symbols must be unique.")
        return symbols

    @field_validator("correlation_block_threshold")
    @classmethod
    def validate_correlation_thresholds(cls, value: float, info: Any) -> float:
        reduce_threshold = info.data.get("correlation_reduce_threshold")
        if reduce_threshold is not None and value <= reduce_threshold:
            raise ValueError(
                "CORRELATION_BLOCK_THRESHOLD must be greater than CORRELATION_REDUCE_THRESHOLD."
            )
        return value


# ---------------------------------------------------------------------------
# Runtime overrides (UI-managed configuration)
# ---------------------------------------------------------------------------


def runtime_config_path() -> Path:
    """Return the JSON file used for UI-managed trading overrides."""
    configured = os.getenv("RUNTIME_CONFIG_PATH", "").strip()
    return Path(configured) if configured else _DEFAULT_RUNTIME_CONFIG_PATH


def load_runtime_overrides(path: Path | None = None) -> dict[str, Any]:
    """
    Read UI-managed trading overrides from disk.

    During tests the default runtime file is ignored so a developer's local
    configuration cannot make the suite non-deterministic. Tests that exercise
    this loader pass an explicit ``path`` or set ``RUNTIME_CONFIG_PATH``.
    """
    if path is None:
        explicit = os.getenv("RUNTIME_CONFIG_PATH", "").strip()
        if not explicit and "pytest" in sys.modules:
            return {}
        path = runtime_config_path()

    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("runtime_config_load_failed path=%s error=%s", path, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("runtime_config_invalid_payload path=%s", path)
        return {}

    return {key: value for key, value in raw.items() if key in RUNTIME_TRADING_FIELDS}


def _validated_with_overrides(base: Settings, overrides: dict[str, Any]) -> Settings:
    data = base.model_dump()
    data.update(overrides)
    return Settings.model_validate(data)


def apply_runtime_overrides(target: Settings, overrides: dict[str, Any]) -> Settings:
    """
    Apply validated overrides in-place.

    Services hold a reference to the cached Settings instance, so mutating it is
    what makes a configuration change effective immediately without a restart.
    """
    filtered = {key: value for key, value in overrides.items() if key in RUNTIME_TRADING_FIELDS}
    if not filtered:
        return target

    validated = _validated_with_overrides(target, filtered)
    for field_name in Settings.model_fields:
        setattr(target, field_name, getattr(validated, field_name))
    return target


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings.

    Using lru_cache ensures settings are parsed from environment
    only once per process. In tests, clear with get_settings.cache_clear().
    UI-managed trading overrides are merged on top of the .env values.
    """
    base = Settings()
    overrides = load_runtime_overrides()
    if not overrides:
        return base
    try:
        return _validated_with_overrides(base, overrides)
    except Exception as exc:
        logger.warning("runtime_config_apply_failed error=%s", exc)
        return base


# Convenient alias
settings = get_settings()
