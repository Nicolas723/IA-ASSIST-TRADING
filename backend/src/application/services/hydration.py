"""
Paper exchange rehydration.

Rebuilds the in-memory :class:`PaperExchange` state from the durable journal at
application startup, so the virtual account resumes exactly where it left off
instead of silently starting from scratch on every restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.domain.market.entities import Exchange
from src.domain.trading.entities import ExecutionMode
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.live_trading_service import LiveTradingService
    from src.domain.trading.ports import TradingRepositoryPort
    from src.infrastructure.exchange.paper import PaperExchange

logger = get_logger(__name__)


class HydrationReport(BaseModel):
    """Summary of what was restored from the journal."""

    positions_restored: int = 0
    orders_restored: int = 0
    trades_restored: int = 0
    balance_restored: bool = False


class LiveHydrationReport(HydrationReport):
    """Hydration summary for the real-trading ledger."""

    exchanges: list[str] = Field(default_factory=list)


async def hydrate_paper_exchange(
    paper_exchange: PaperExchange,
    repo: TradingRepositoryPort,
    *,
    mode: ExecutionMode = ExecutionMode.PAPER,
    exchange: Exchange = Exchange.PAPER,
    order_limit: int = 1000,
    trade_limit: int = 5000,
) -> HydrationReport:
    """
    Load persisted trading state into ``paper_exchange``.

    The latest portfolio snapshot provides the cash balance, realized PnL and
    peak equity; positions/orders/trades are restored verbatim. If no snapshot
    exists (fresh install) the exchange keeps its default balance.
    """
    positions = await repo.list_positions(mode=mode)
    orders = await repo.list_orders(mode=mode, limit=order_limit)
    trades = await repo.list_trades(mode=mode, limit=trade_limit)
    snapshot = await repo.latest_portfolio_snapshot(mode, exchange)

    paper_exchange.restore(
        positions=positions,
        orders=orders,
        trades=trades,
        available_balance=snapshot.available_balance if snapshot is not None else None,
        realized_pnl=snapshot.total_realized_pnl if snapshot is not None else None,
        peak_equity=snapshot.peak_value if snapshot is not None else None,
    )

    report = HydrationReport(
        positions_restored=len(positions),
        orders_restored=len(orders),
        trades_restored=len(trades),
        balance_restored=snapshot is not None,
    )
    logger.info("paper_exchange_hydrated", **report.model_dump())
    return report


async def hydrate_from_database(paper_exchange: PaperExchange) -> HydrationReport:
    """
    Convenience wrapper that opens its own DB session.

    Used by the application lifespan; infrastructure imports are local so the
    application module stays importable without a live database.
    """
    from src.infrastructure.persistence.database import get_db_session
    from src.infrastructure.persistence.repositories.trading_repository import (
        SqlTradingRepository,
    )

    async with get_db_session() as session:
        return await hydrate_paper_exchange(
            paper_exchange,
            SqlTradingRepository(session),
        )


async def hydrate_live_trading_service(
    live_service: LiveTradingService,
    repo: TradingRepositoryPort,
    *,
    order_limit: int = 1000,
    trade_limit: int = 5000,
) -> LiveHydrationReport:
    """
    Restore the real-trading ledger from the durable journal.

    This is a best-effort recovery of positions, orders, trades and the latest
    equity snapshot. Exchange balances remain the source of truth: the guard
    reconciles tracked positions with the exchange shortly after startup.
    """
    positions = await repo.list_positions(mode=ExecutionMode.REAL)
    orders = await repo.list_orders(mode=ExecutionMode.REAL, limit=order_limit)
    trades = await repo.list_trades(mode=ExecutionMode.REAL, limit=trade_limit)

    exchanges = sorted({position.exchange.value for position in positions})
    try:
        configured = Exchange(live_service.settings.real_trading_exchange)
    except ValueError:
        configured = Exchange.BINANCE
    snapshot_exchange = (
        configured if configured.value in exchanges or not exchanges else Exchange(exchanges[0])
    )
    snapshot = await repo.latest_portfolio_snapshot(ExecutionMode.REAL, snapshot_exchange)

    live_service.restore(
        positions=positions,
        orders=orders,
        trades=trades,
        available_balance=snapshot.available_balance if snapshot is not None else None,
        realized_pnl=snapshot.total_realized_pnl if snapshot is not None else None,
        peak_value=snapshot.peak_value if snapshot is not None else None,
    )

    report = LiveHydrationReport(
        positions_restored=len(positions),
        orders_restored=len(orders),
        trades_restored=len(trades),
        balance_restored=snapshot is not None,
        exchanges=exchanges,
    )
    logger.info("live_trading_hydrated", **report.model_dump())
    return report


async def hydrate_live_from_database(
    live_service: LiveTradingService,
) -> LiveHydrationReport:
    """Convenience wrapper that opens its own DB session for the real ledger."""
    from src.infrastructure.persistence.database import get_db_session
    from src.infrastructure.persistence.repositories.trading_repository import (
        SqlTradingRepository,
    )

    async with get_db_session() as session:
        return await hydrate_live_trading_service(
            live_service,
            SqlTradingRepository(session),
        )
