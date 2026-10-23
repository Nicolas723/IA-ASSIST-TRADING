"""
FastAPI Application — Main entry point.

Assembles all layers, configures middleware, registers routes,
and manages application lifecycle (startup/shutdown).

Architecture:
- Lifespan context manager handles startup/shutdown (FastAPI v0.93+)
- All dependencies injected via FastAPI's DI system
- Middleware applied in order (outermost first)
- No business logic here — only wiring and configuration
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from src.infrastructure.cache.redis_client import create_redis_client
from src.infrastructure.persistence.database import dispose_engine
from src.interfaces.api.v1 import (
    agents,
    alerts,
    audit,
    autonomous,
    backtesting,
    market,
    migration,
    observability,
    portfolio,
    risk,
    status,
    strategy,
    trading,
    vault,
)
from src.interfaces.websocket import market_stream
from src.shared.config.settings import get_settings
from src.shared.exceptions.domain import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    CircuitBreakerError,
    DomainError,
    NotFoundError,
    RiskViolationError,
    ValidationError,
)
from src.shared.logging.setup import (
    bind_request_context,
    clear_request_context,
    get_logger,
    setup_logging,
)
from src.shared.observability.metrics import (
    http_request_duration_seconds,
    http_requests_total,
    initialize_metrics,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from src.domain.trading.entities import Order

settings = get_settings()

# ---------------------------------------------------------------------------
# Initialize logging (must happen before any logger is used)
# ---------------------------------------------------------------------------

setup_logging(
    log_level=settings.log_level.value,
    log_format=settings.log_format.value,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sentry (error tracking)
# ---------------------------------------------------------------------------

if settings.sentry_dsn:
    try:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            environment=settings.app_env.value,
            release=settings.app_version,
        )
        logger.info("sentry_initialized", dsn_configured=True)
    except Exception as e:
        logger.warning("sentry_init_failed", error=str(e))


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """
    Manage application startup and shutdown.

    Startup:
    - Initialize Redis connection
    - Verify database connectivity
    - Initialize Prometheus metrics
    - Load plugin registry

    Shutdown:
    - Close Redis connections
    - Dispose DB engine
    - Flush pending metrics
    """
    global _redis_client

    logger.info(
        "application_starting",
        app_name=settings.app_name,
        version=settings.app_version,
        env=settings.app_env.value,
        execution_mode=settings.execution_mode.value,
    )

    # Initialize metrics
    initialize_metrics(settings.app_version, settings.app_env.value)

    # Wire the live WebSocket price feed to the real exchange ticker (CCXT).
    # Falls back to the simulated feed only if this wiring or the fetch fails.
    try:
        from src.interfaces.api.v1.deps import get_market_data_fetcher

        async def _ws_price_provider(symbol: str) -> float | None:
            fetcher = get_market_data_fetcher()
            return float(await fetcher.fetch_last_price(symbol))

        market_stream.manager.set_price_provider(_ws_price_provider)
        logger.info("ws_live_price_provider_wired")
    except Exception as e:
        logger.warning("ws_live_price_provider_wiring_failed", error=str(e))

    # Rehydrate the virtual paper account from the durable journal so open
    # positions, cash balance and PnL survive process restarts.
    try:
        from src.application.services.hydration import hydrate_from_database
        from src.interfaces.api.v1.deps import get_paper_exchange

        report = await hydrate_from_database(get_paper_exchange())
        logger.info("paper_account_rehydrated", **report.model_dump())
    except Exception as e:
        logger.warning("paper_account_rehydration_failed", error=str(e))

    # Real-trading protective guard: local SL/TP monitor for real positions.
    try:
        from src.application.services.journal import (
            persist_live_order_to_database,
            persist_live_positions_to_database,
        )
        from src.interfaces.api.v1.deps import (
            get_live_trading_service,
            get_market_data_fetcher,
        )

        live_trading_service = get_live_trading_service()

        # Restore the durable real-trading ledger before the guard starts.
        try:
            from src.application.services.hydration import hydrate_live_from_database

            live_report = await hydrate_live_from_database(live_trading_service)
            logger.info("live_trading_rehydrated", **live_report.model_dump())
            await live_trading_service.notify_restored_positions()
        except Exception as e:
            logger.warning("live_trading_rehydration_failed", error=str(e))

        async def _guard_price(symbol: str) -> float | None:
            fetcher = get_market_data_fetcher()
            return float(await fetcher.fetch_last_price(symbol))

        async def _guard_persist(order: Order) -> None:
            await persist_live_order_to_database(order, live_trading_service)

        async def _guard_reconcile() -> None:
            await persist_live_positions_to_database(live_trading_service)

        live_trading_service.start_guard(_guard_price, _guard_persist, _guard_reconcile)
        logger.info(
            "live_trading_guard_started",
            positions=len(live_trading_service.get_positions()),
        )
    except Exception as e:
        logger.warning("live_trading_guard_start_failed", error=str(e))

    # Initialize Redis
    import src.infrastructure.cache.redis_client as rc_module

    rc_module._redis_client = create_redis_client()
    redis_ok = await rc_module._redis_client.ping()
    if not redis_ok:
        logger.error("redis_connection_failed")
    else:
        logger.info("redis_connected")

    # Safety check: prevent accidental real trading
    if settings.real_trading_enabled:
        logger.warning(
            "real_trading_enabled",
            message="⚠️  REAL TRADING IS ENABLED. Orders will be executed with real funds.",
        )
        try:
            from src.domain.notifications.entities import AlertSeverity
            from src.interfaces.api.v1.deps import get_alert_service

            await get_alert_service().notify(
                "Trading real activado",
                "El backend ha arrancado con trading real habilitado. "
                "Las órdenes confirmadas se enviarán al exchange.",
                AlertSeverity.CRITICAL,
                {"execution_mode": settings.execution_mode.value},
            )
        except Exception as e:
            logger.warning("startup_alert_failed", error=str(e))
    else:
        logger.info(
            "paper_mode_active", message="Paper trading mode — no real orders will be placed."
        )

    logger.info("application_ready", host=settings.server_host, port=settings.server_port)

    yield  # Application runs here

    # --------------- Shutdown ---------------
    logger.info("application_shutting_down")

    try:
        from src.interfaces.api.v1.deps import _autonomous_engine

        if _autonomous_engine and _autonomous_engine.is_running:
            await _autonomous_engine.stop()
            logger.info("autonomous_engine_stopped")
    except Exception as e:
        logger.warning("autonomous_engine_stop_failed", error=str(e))

    try:
        from src.interfaces.api.v1.deps import get_live_trading_service

        await get_live_trading_service().stop_guard()
        logger.info("live_trading_guard_stopped")
    except Exception as e:
        logger.warning("live_trading_guard_stop_failed", error=str(e))

    if rc_module._redis_client:
        await rc_module._redis_client.close()
        logger.info("redis_disconnected")

    await dispose_engine()
    logger.info("application_stopped")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title=settings.app_name,
        description="Professional AI Trading Assistant — Multi-agent, multi-exchange, multi-LLM",
        version=settings.app_version,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # -------------------------------------------------------------------------
    # Middleware (applied in reverse order — last added = outermost)
    # -------------------------------------------------------------------------

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------------
    # Request ID + Logging + Metrics middleware
    # -------------------------------------------------------------------------

    @app.middleware("http")
    async def request_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Inject request context, log requests, and record metrics."""
        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

        # Bind context for structured logging
        bind_request_context(request_id=request_id)

        # Add request ID to response headers
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            duration = time.monotonic() - start_time

            # Record Prometheus metrics
            endpoint = request.url.path
            http_requests_total.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=str(response.status_code),
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=endpoint,
            ).observe(duration)

            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = f"{duration:.4f}"

            logger.info(
                "request_completed",
                method=request.method,
                path=endpoint,
                status_code=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )

        except Exception as exc:
            duration = time.monotonic() - start_time
            logger.exception(
                "request_error",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration * 1000, 2),
                error=str(exc),
            )
            raise

        finally:
            clear_request_context()

        return response

    # -------------------------------------------------------------------------
    # Exception handlers
    # -------------------------------------------------------------------------

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": exc.code,
                "message": exc.message,
                "field": exc.field,
                "details": exc.details,
            },
        )

    @app.exception_handler(AuthenticationError)
    async def auth_error_handler(_request: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(AuthorizationError)
    async def authz_error_handler(_request: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(RiskViolationError)
    async def risk_error_handler(_request: Request, exc: RiskViolationError) -> JSONResponse:
        logger.error(
            "risk_violation",
            code=exc.code,
            severity=exc.severity,
            message=exc.message,
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": exc.code,
                "message": exc.message,
                "severity": exc.severity,
                "details": exc.details,
            },
        )

    @app.exception_handler(CircuitBreakerError)
    async def circuit_breaker_handler(_request: Request, exc: CircuitBreakerError) -> JSONResponse:
        logger.critical("circuit_breaker_tripped", message=exc.message)
        return JSONResponse(
            status_code=503,
            content={
                "error": "CIRCUIT_BREAKER_ACTIVE",
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": exc.code, "message": exc.message},
        )

    # -------------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------------

    # Health endpoints (no auth required)
    @app.get("/health", tags=["Health"], summary="Basic health check")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/ready", tags=["Health"], summary="Readiness probe (checks dependencies)")
    async def readiness() -> dict[str, object]:
        import src.infrastructure.cache.redis_client as rc_module

        redis_ok = await rc_module._redis_client.ping() if rc_module._redis_client else False
        ready = redis_ok
        return {
            "status": "ready" if ready else "not_ready",
            "checks": {
                "redis": "ok" if redis_ok else "error",
            },
        }

    @app.get("/live", tags=["Health"], summary="Liveness probe")
    async def liveness() -> dict[str, str]:
        return {"status": "alive"}

    # API v1 routers
    app.include_router(market.router, prefix="/api/v1/market", tags=["Market Data"])
    app.include_router(trading.router, prefix="/api/v1/trading", tags=["Trading"])
    app.include_router(portfolio.router, prefix="/api/v1/portfolio", tags=["Portfolio"])
    app.include_router(strategy.router, prefix="/api/v1/strategy", tags=["Strategy"])
    app.include_router(backtesting.router, prefix="/api/v1/backtesting", tags=["Backtesting"])
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["AI Agents"])
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["Audit"])
    app.include_router(risk.router, prefix="/api/v1/risk", tags=["Risk"])
    app.include_router(vault.router, prefix="/api/v1/vault", tags=["Credential Vault"])
    app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
    app.include_router(
        observability.router, prefix="/api/v1/observability", tags=["Observability & MLOps"]
    )
    app.include_router(
        autonomous.router, prefix="/api/v1/autonomous", tags=["Autonomous Trading Bot"]
    )

    app.include_router(migration.router, prefix="/api/v1", tags=["Migration"])
    app.include_router(status.router, prefix="/api/v1", tags=["Status & Logs"])
    app.include_router(market_stream.router, prefix="/api/v1", tags=["WebSocket"])

    # Prometheus metrics endpoint
    if settings.metrics_enabled:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

    return app


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = create_app()


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.is_development,
        log_config=None,  # Use our structlog setup instead
        workers=1 if settings.is_development else settings.server_workers,
    )
