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


# ---------------------------------------------------------------------------
# Nested Settings Groups
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")

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
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    url: RedisDsn = Field(default=cast("RedisDsn", "redis://localhost:6379/0"))
    max_connections: int = Field(default=50, ge=1)
    password: str | None = None


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", extra="ignore")

    api_key: str = Field(default="")
    default_model: str = Field(default="gpt-4o")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_", extra="ignore")

    api_key: str = Field(default="")
    default_model: str = Field(default="claude-opus-4-5")
    max_tokens: int = Field(default=8192)


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOOGLE_", extra="ignore")

    api_key: str = Field(default="")
    default_model: str = Field(default="gemini-2.5-flash")


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_", extra="ignore")

    base_url: str = Field(default="http://localhost:11434")
    default_model: str = Field(default="llama3.2")
    embedding_model: str = Field(default="nomic-embed-text")


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAX_", extra="ignore")

    daily_loss_pct: Decimal = Field(default=Decimal("2.0"), gt=Decimal("0"))
    position_size_pct: Decimal = Field(default=Decimal("5.0"), gt=Decimal("0"))
    portfolio_risk_pct: Decimal = Field(default=Decimal("20.0"), gt=Decimal("0"))
    open_positions: int = Field(default=10, ge=1)
    max_drawdown_pct: Decimal = Field(default=Decimal("10.0"), gt=Decimal("0"))
    max_risk_per_trade_pct: Decimal = Field(default=Decimal("1.5"), gt=Decimal("0"))
    max_open_positions: int = Field(default=5, ge=1)


class AgentLLMConfig(BaseSettings):
    """Per-agent LLM provider configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    technical_analyst_provider: LLMProvider = LLMProvider.OPENAI
    technical_analyst_model: str = "gpt-4o"
    price_action_provider: LLMProvider = LLMProvider.OPENAI
    price_action_model: str = "gpt-4o"
    sentiment_provider: LLMProvider = LLMProvider.GOOGLE
    sentiment_model: str = "gemini-2.5-flash"
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
    risk: RiskSettings = Field(default_factory=RiskSettings)
    agent_llm: AgentLLMConfig = Field(default_factory=AgentLLMConfig)

    # -------------------------------------------------------------------------
    # Trading
    # -------------------------------------------------------------------------
    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER)
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
    cache_ttl_seconds: int = Field(default=300)

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


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings.

    Using lru_cache ensures settings are parsed from environment
    only once per process. In tests, clear with get_settings.cache_clear().
    """
    return Settings()


# Convenient alias
settings = get_settings()
