"""
Prometheus metrics definitions.

Design decision: Define all metrics centrally to avoid duplicate registration
and to have a clear inventory of what the system measures.

Metrics follow the OpenMetrics naming convention:
  {namespace}_{subsystem}_{name}_{unit}
  e.g., trading_orders_total, market_data_latency_seconds
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ---------------------------------------------------------------------------
# Application Info
# ---------------------------------------------------------------------------

app_info = Info(
    "trading_app",
    "AI Trading Assistant application information",
)

# ---------------------------------------------------------------------------
# HTTP / API Metrics
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ---------------------------------------------------------------------------
# Market Data Metrics
# ---------------------------------------------------------------------------

market_data_ingested_total = Counter(
    "market_data_ingested_total",
    "Total market data points ingested",
    ["symbol", "exchange", "timeframe"],
)

market_data_latency_seconds = Histogram(
    "market_data_latency_seconds",
    "Market data fetch latency",
    ["exchange", "data_type"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

websocket_connections_active = Gauge(
    "websocket_connections_active",
    "Number of active WebSocket connections",
    ["exchange", "symbol"],
)

# ---------------------------------------------------------------------------
# Trading Metrics
# ---------------------------------------------------------------------------

orders_placed_total = Counter(
    "orders_placed_total",
    "Total orders placed",
    ["mode", "exchange", "symbol", "side", "order_type"],
)

orders_failed_total = Counter(
    "orders_failed_total",
    "Total orders that failed",
    ["mode", "exchange", "symbol", "reason"],
)

positions_open = Gauge(
    "positions_open",
    "Number of currently open positions",
    ["mode", "exchange"],
)

portfolio_value_usd = Gauge(
    "portfolio_value_usd",
    "Current portfolio value in USD",
    ["mode"],
)

daily_pnl_usd = Gauge(
    "daily_pnl_usd",
    "Current daily PnL in USD",
    ["mode"],
)

# ---------------------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------------------

risk_violations_total = Counter(
    "risk_violations_total",
    "Total risk rule violations",
    ["violation_type", "severity"],
)

circuit_breaker_activations_total = Counter(
    "circuit_breaker_activations_total",
    "Total circuit breaker activations",
    ["reason"],
)

drawdown_current_pct = Gauge(
    "drawdown_current_pct",
    "Current drawdown percentage from peak",
    ["mode"],
)

# ---------------------------------------------------------------------------
# AI Agent Metrics
# ---------------------------------------------------------------------------

agent_analyses_total = Counter(
    "agent_analyses_total",
    "Total analyses performed by agents",
    ["agent_type", "symbol", "signal"],
)

agent_analysis_duration_seconds = Histogram(
    "agent_analysis_duration_seconds",
    "Agent analysis duration in seconds",
    ["agent_type", "llm_provider"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

agent_confidence_score = Histogram(
    "agent_confidence_score",
    "Distribution of agent confidence scores",
    ["agent_type"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

llm_requests_total = Counter(
    "llm_requests_total",
    "Total LLM API requests",
    ["provider", "model", "agent_type"],
)

llm_tokens_used_total = Counter(
    "llm_tokens_used_total",
    "Total LLM tokens consumed",
    ["provider", "model", "token_type"],  # token_type: prompt | completion
)

llm_errors_total = Counter(
    "llm_errors_total",
    "Total LLM API errors",
    ["provider", "error_type"],
)

# ---------------------------------------------------------------------------
# Infrastructure Metrics
# ---------------------------------------------------------------------------

db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Database query duration",
    ["operation", "table"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

cache_hits_total = Counter(
    "cache_hits_total",
    "Total Redis cache hits",
    ["cache_type"],
)

cache_misses_total = Counter(
    "cache_misses_total",
    "Total Redis cache misses",
    ["cache_type"],
)

# ---------------------------------------------------------------------------
# Initialization helper
# ---------------------------------------------------------------------------


def initialize_metrics(app_version: str, app_env: str) -> None:
    """Set static app info labels."""
    app_info.info(
        {
            "version": app_version,
            "environment": app_env,
        }
    )
