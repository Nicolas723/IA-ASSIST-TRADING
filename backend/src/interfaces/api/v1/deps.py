"""
API Dependencies — FastAPI Dependency Injection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.agent_service import AgentService
from src.application.services.alert_service import AlertService
from src.application.services.autonomous_engine import (
    AutonomousCycleServices,
    AutonomousEngine,
)
from src.application.services.concentration_service import ConcentrationService
from src.application.services.indicator_service import IndicatorService
from src.application.services.journal import (
    persist_live_order_to_database,
    persist_order_to_database,
)
from src.application.services.live_trading_service import LiveTradingService
from src.application.services.market_data_service import MarketDataService
from src.application.services.observability_service import ObservabilityService
from src.application.services.optimization_service import OptimizationService
from src.application.services.regime_service import RegimeService
from src.application.services.risk_service import RiskService
from src.application.services.runtime_config_service import RuntimeConfigService
from src.application.services.strategy_service import StrategyService
from src.application.services.trading_service import TradingService
from src.application.services.vault_service import VaultService
from src.domain.agents.coordinator import AgentCoordinator
from src.domain.indicators.builtin import (
    ATRIndicator,
    BollingerBandsIndicator,
    EMASuiteIndicator,
    MACDIndicator,
    RSIIndicator,
)
from src.infrastructure.exchange.ccxt_base import CcxtBaseAdapter
from src.infrastructure.exchange.market_data_factory import create_market_data_adapter
from src.infrastructure.exchange.paper import PaperExchange
from src.infrastructure.llm.factory import create_llm_gateway
from src.infrastructure.llm.gateway import LLMGateway
from src.infrastructure.market_data.rest_fetcher import RetryingMarketDataFetcher
from src.infrastructure.persistence.database import get_db, get_db_session
from src.infrastructure.persistence.repositories.candle_repository import TimescaleCandleRepository
from src.infrastructure.persistence.repositories.decision_repository import SqlDecisionRepository
from src.infrastructure.persistence.repositories.research_repository import SqlResearchRepository
from src.infrastructure.persistence.repositories.shadow_scan_repository import (
    SqlShadowScanRepository,
)
from src.infrastructure.persistence.repositories.trading_repository import SqlTradingRepository
from src.infrastructure.security.credential_vault import CredentialVault
from src.shared.config.settings import get_settings
from src.shared.plugins.registry import PluginRegistry

if TYPE_CHECKING:
    from src.domain.agents.entities import CoordinatorDecision
    from src.domain.trading.entities import Order

# Shared in-memory audit trail. Request handlers and per-cycle background
# services both append to it so the UI history does not reset every cycle.
_decision_history: list[CoordinatorDecision] = []

# Single shared adapter instance
_market_data_adapter: CcxtBaseAdapter | None = None
_fetcher: RetryingMarketDataFetcher | None = None
_strategy_service: StrategyService | None = None
_risk_service: RiskService | None = None
_paper_exchange: PaperExchange | None = None
_trading_service: TradingService | None = None
_agent_service: AgentService | None = None
_optimization_service: OptimizationService | None = None
_vault_service: VaultService | None = None
_observability_service: ObservabilityService | None = None
_autonomous_engine: AutonomousEngine | None = None
_alert_service: AlertService | None = None
_live_trading_service: LiveTradingService | None = None
_runtime_config_service: RuntimeConfigService | None = None
_llm_gateway: LLMGateway | None = None
_llm_gateway_initialized = False


def get_market_data_fetcher() -> RetryingMarketDataFetcher:
    global _market_data_adapter, _fetcher
    if _fetcher is None:
        _market_data_adapter = create_market_data_adapter(get_settings().market_data_exchange)
        _fetcher = RetryingMarketDataFetcher(_market_data_adapter)
    return _fetcher


def get_llm_gateway() -> LLMGateway | None:
    """Share one provider gateway across all committee specialists."""
    global _llm_gateway, _llm_gateway_initialized
    if not _llm_gateway_initialized:
        _llm_gateway = create_llm_gateway()
        _llm_gateway_initialized = True
    return _llm_gateway


def get_market_data_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    fetcher: Annotated[RetryingMarketDataFetcher, Depends(get_market_data_fetcher)],
) -> MarketDataService:
    repository = TimescaleCandleRepository(session)
    return MarketDataService(exchange=fetcher, repository=repository)


def get_regime_service(
    market_service: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> RegimeService:
    """Instantiate the read-only market-regime classifier."""
    return RegimeService(market_service)


def get_concentration_service(
    market_service: Annotated[MarketDataService, Depends(get_market_data_service)],
    risk_service: Annotated[RiskService, Depends(get_risk_service)],
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    live_trading_service: Annotated[LiveTradingService, Depends(get_live_trading_service)],
) -> ConcentrationService:
    """Instantiate the read-only concentration and correlation report."""
    return ConcentrationService(
        market_service=market_service,
        risk_service=risk_service,
        trading_service=trading_service,
        live_trading_service=live_trading_service,
    )


def get_indicator_service() -> IndicatorService:
    """Instantiate IndicatorService with built-ins + discovered plugins."""
    service = IndicatorService(
        [
            RSIIndicator(),
            MACDIndicator(),
            EMASuiteIndicator(),
            BollingerBandsIndicator(),
            ATRIndicator(),
        ]
    )
    # Add discovered plugins
    for ind_name in PluginRegistry.list_indicators():
        ind_cls = PluginRegistry.get_indicator(ind_name)
        if ind_cls:
            service.add_indicator(ind_cls())
    return service


def get_strategy_service() -> StrategyService:
    """Instantiate singleton StrategyService."""
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = StrategyService()
    return _strategy_service


def get_risk_service() -> RiskService:
    """Instantiate singleton RiskService."""
    global _risk_service
    if _risk_service is None:
        _risk_service = RiskService()
    return _risk_service


def get_paper_exchange() -> PaperExchange:
    global _paper_exchange
    if _paper_exchange is None:
        _paper_exchange = PaperExchange()
    return _paper_exchange


def get_trading_service(
    paper_exchange: Annotated[PaperExchange, Depends(get_paper_exchange)],
    risk_service: Annotated[RiskService, Depends(get_risk_service)],
) -> TradingService:
    global _trading_service
    if _trading_service is None:
        _trading_service = TradingService(
            paper_exchange=paper_exchange,
            risk_service=risk_service,
        )
    return _trading_service


def get_trading_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SqlTradingRepository:
    """Trading journal repository bound to the request session."""
    return SqlTradingRepository(session)


def get_decision_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SqlDecisionRepository:
    """AI decision audit repository bound to the request session."""
    return SqlDecisionRepository(session)


def get_shadow_scan_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SqlShadowScanRepository:
    """Repository for read-only market scan history."""
    return SqlShadowScanRepository(session)


def get_research_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SqlResearchRepository:
    """Durable, versioned research memory bound to the request session."""
    return SqlResearchRepository(session)


def get_agent_service(
    market_service: Annotated[MarketDataService, Depends(get_market_data_service)],
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
    research_repository: Annotated[SqlResearchRepository, Depends(get_research_repository)],
    llm_gateway: Annotated[LLMGateway | None, Depends(get_llm_gateway)],
) -> AgentService:
    global _agent_service
    if _agent_service is None:
        coordinator = AgentCoordinator(llm_gateway=llm_gateway)
        _agent_service = AgentService(
            coordinator=coordinator,
            market_service=market_service,
            decision_repository=decision_repository,
            research_repository=research_repository,
            decision_history=_decision_history,
        )
    else:
        _agent_service.market_service = market_service
        _agent_service.decision_repository = decision_repository
        _agent_service.research_repository = research_repository
    return _agent_service


def get_optimization_service(
    market_service: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> OptimizationService:
    global _optimization_service
    if _optimization_service is None:
        _optimization_service = OptimizationService(market_service=market_service)
    else:
        _optimization_service.market_service = market_service
    return _optimization_service


def get_vault_service() -> VaultService:
    global _vault_service
    if _vault_service is None:
        _vault_service = VaultService(vault=CredentialVault())
        _vault_service.set_active_environment(get_settings().trading_environment.value)
    return _vault_service


def get_runtime_config_service() -> RuntimeConfigService:
    """Singleton for UI-managed trading overrides."""
    global _runtime_config_service
    if _runtime_config_service is None:
        _runtime_config_service = RuntimeConfigService(
            settings=get_settings(),
            vault_service=get_vault_service(),
        )
    return _runtime_config_service


def get_observability_service() -> ObservabilityService:
    global _observability_service
    if _observability_service is None:
        _observability_service = ObservabilityService()
    return _observability_service


def get_alert_service() -> AlertService:
    """Singleton alert fan-out service configured from env."""
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService.from_settings(get_settings())
    return _alert_service


def get_live_trading_service() -> LiveTradingService:
    """Singleton real-trading service with vault + risk + alert wiring."""
    global _live_trading_service
    if _live_trading_service is None:
        _live_trading_service = LiveTradingService(
            vault_service=get_vault_service(),
            risk_service=get_risk_service(),
            alert_service=get_alert_service(),
        )
    return _live_trading_service


@asynccontextmanager
async def autonomous_cycle_services() -> AsyncIterator[AutonomousCycleServices]:
    """Build services for exactly one background cycle.

    The daemon outlives the HTTP request that starts it. Reusing the request's
    ``AsyncSession`` caused ``IllegalStateChangeError`` because FastAPI closed
    the session while the cycle was still using it. Opening a session here gives
    every cycle an independent, fully owned connection.
    """
    async with get_db_session() as session:
        market_service = MarketDataService(
            exchange=get_market_data_fetcher(),
            repository=TimescaleCandleRepository(session),
        )
        agent_service = AgentService(
            coordinator=AgentCoordinator(llm_gateway=get_llm_gateway()),
            market_service=market_service,
            decision_repository=SqlDecisionRepository(session),
            research_repository=SqlResearchRepository(session),
            decision_history=_decision_history,
        )
        yield AutonomousCycleServices(
            market_service=market_service,
            agent_service=agent_service,
        )


def get_autonomous_engine(
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    risk_service: Annotated[RiskService, Depends(get_risk_service)],
) -> AutonomousEngine:
    """Singleton daemon that owns its database sessions per cycle.

    It deliberately does not depend on request-scoped services: those are bound
    to a session FastAPI closes as soon as the response is sent.
    """
    global _autonomous_engine
    if _autonomous_engine is None:
        _autonomous_engine = AutonomousEngine(
            trading_service=trading_service,
            risk_service=risk_service,
            journal=persist_order_to_database,
            live_trading_service=get_live_trading_service(),
            journal_real=_persist_live_order,
            alert_service=get_alert_service(),
            cycle_service_factory=autonomous_cycle_services,
        )
    else:
        _autonomous_engine.trading_service = trading_service
        _autonomous_engine.risk_service = risk_service
        _autonomous_engine.live_trading_service = get_live_trading_service()
        _autonomous_engine.journal = persist_order_to_database
        _autonomous_engine.journal_real = _persist_live_order
        _autonomous_engine.alert_service = get_alert_service()
    return _autonomous_engine


async def _persist_live_order(order: Order) -> bool:
    """Journal callback bound to the singleton live service."""
    return await persist_live_order_to_database(order, get_live_trading_service())
