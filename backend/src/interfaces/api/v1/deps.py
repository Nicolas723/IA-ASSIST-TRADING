"""
API Dependencies — FastAPI Dependency Injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.agent_service import AgentService
from src.application.services.alert_service import AlertService
from src.application.services.autonomous_engine import AutonomousEngine
from src.application.services.indicator_service import IndicatorService
from src.application.services.journal import (
    persist_live_order_to_database,
    persist_order_to_database,
)
from src.application.services.live_trading_service import LiveTradingService
from src.application.services.market_data_service import MarketDataService
from src.application.services.observability_service import ObservabilityService
from src.application.services.optimization_service import OptimizationService
from src.application.services.risk_service import RiskService
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
from src.infrastructure.exchange.binance import BinanceAdapter
from src.infrastructure.exchange.paper import PaperExchange
from src.infrastructure.market_data.rest_fetcher import RetryingMarketDataFetcher
from src.infrastructure.persistence.database import get_db
from src.infrastructure.persistence.repositories.candle_repository import TimescaleCandleRepository
from src.infrastructure.persistence.repositories.decision_repository import SqlDecisionRepository
from src.infrastructure.persistence.repositories.trading_repository import SqlTradingRepository
from src.infrastructure.security.credential_vault import CredentialVault
from src.shared.config.settings import get_settings
from src.shared.plugins.registry import PluginRegistry

if TYPE_CHECKING:
    from src.domain.trading.entities import Order

# Single shared adapter instance
_binance_adapter: BinanceAdapter | None = None
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


def get_market_data_fetcher() -> RetryingMarketDataFetcher:
    global _binance_adapter, _fetcher
    if _fetcher is None:
        _binance_adapter = BinanceAdapter()
        _fetcher = RetryingMarketDataFetcher(_binance_adapter)
    return _fetcher


def get_market_data_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    fetcher: Annotated[RetryingMarketDataFetcher, Depends(get_market_data_fetcher)],
) -> MarketDataService:
    repository = TimescaleCandleRepository(session)
    return MarketDataService(exchange=fetcher, repository=repository)


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


def get_agent_service(
    market_service: Annotated[MarketDataService, Depends(get_market_data_service)],
    decision_repository: Annotated[SqlDecisionRepository, Depends(get_decision_repository)],
) -> AgentService:
    global _agent_service
    if _agent_service is None:
        coordinator = AgentCoordinator()
        _agent_service = AgentService(
            coordinator=coordinator,
            market_service=market_service,
            decision_repository=decision_repository,
        )
    else:
        _agent_service.market_service = market_service
        _agent_service.decision_repository = decision_repository
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
    return _vault_service


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


def get_autonomous_engine(
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    market_service: Annotated[MarketDataService, Depends(get_market_data_service)],
    risk_service: Annotated[RiskService, Depends(get_risk_service)],
) -> AutonomousEngine:
    global _autonomous_engine
    if _autonomous_engine is None:
        _autonomous_engine = AutonomousEngine(
            agent_service=agent_service,
            trading_service=trading_service,
            market_service=market_service,
            risk_service=risk_service,
            journal=persist_order_to_database,
            live_trading_service=get_live_trading_service(),
            journal_real=_persist_live_order,
            alert_service=get_alert_service(),
        )
    else:
        _autonomous_engine.agent_service = agent_service
        _autonomous_engine.trading_service = trading_service
        _autonomous_engine.market_service = market_service
        _autonomous_engine.risk_service = risk_service
        _autonomous_engine.journal = persist_order_to_database
        _autonomous_engine.live_trading_service = get_live_trading_service()
        _autonomous_engine.journal_real = _persist_live_order
        _autonomous_engine.alert_service = get_alert_service()
    return _autonomous_engine


async def _persist_live_order(order: Order) -> bool:
    """Journal callback bound to the singleton live service."""
    return await persist_live_order_to_database(order, get_live_trading_service())
