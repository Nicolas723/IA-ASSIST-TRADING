"""
Trading journal helpers.

Centralises the "persist an execution" workflow so every actor (HTTP router,
autonomous daemon, future schedulers) writes the same durable state:

1. the order,
2. the matching trade (if it actually filled),
3. the reconciled set of open positions,
4. a portfolio equity snapshot.

All operations are best-effort: journaling must never break a trade that has
already executed at the exchange, so failures are logged and swallowed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.market.entities import Exchange
from src.domain.trading.entities import ExecutionMode, Order
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.application.services.live_trading_service import LiveTradingService
    from src.application.services.trading_service import TradingService
    from src.domain.trading.ports import TradingRepositoryPort

logger = get_logger(__name__)


async def record_execution(
    repo: TradingRepositoryPort,
    trading_service: TradingService | LiveTradingService,
    order: Order,
    mode: ExecutionMode = ExecutionMode.PAPER,
    exchange: Exchange = Exchange.PAPER,
) -> bool:
    """
    Persist an executed order and reconcile the journal.

    Returns ``True`` if the write succeeded, ``False`` if it was skipped or failed.
    """
    try:
        await repo.save_order(order)

        # Persist the trade associated with this order (rejected orders have none).
        trade = next(
            (t for t in trading_service.get_trades(mode) if t.order_id == order.id),
            None,
        )
        if trade is not None:
            await repo.save_trade(trade)

        # Reconcile open positions: upsert current ones, drop closed ones.
        await repo.sync_positions(mode, trading_service.get_positions(mode))

        # Append an equity snapshot so balances can be restored on restart.
        await repo.save_portfolio_snapshot(trading_service.get_portfolio(mode), exchange)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "trade_journal_persist_failed",
            order_id=str(order.id),
            error=str(exc),
        )
        return False


async def persist_order_to_database(
    order: Order,
    trading_service: TradingService,
) -> bool:
    """
    Session-per-call journaling for background actors (the autonomous daemon).

    Injected into ``AutonomousEngine`` by the DI layer so the engine stays
    decoupled from infrastructure and unit tests remain hermetic.
    """
    try:
        from src.infrastructure.persistence.database import get_db_session
        from src.infrastructure.persistence.repositories.trading_repository import (
            SqlTradingRepository,
        )

        async with get_db_session() as session:
            return await record_execution(
                SqlTradingRepository(session),
                trading_service,
                order,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "journal_session_failed",
            order_id=str(order.id),
            error=str(exc),
        )
        return False


async def persist_live_order_to_database(
    order: Order,
    live_service: LiveTradingService,
) -> bool:
    """Session-per-call journaling for real executions (router and SL/TP guard)."""
    try:
        from src.infrastructure.persistence.database import get_db_session
        from src.infrastructure.persistence.repositories.trading_repository import (
            SqlTradingRepository,
        )

        async with get_db_session() as session:
            return await record_execution(
                SqlTradingRepository(session),
                live_service,
                order,
                ExecutionMode.REAL,
                order.exchange,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "live_journal_session_failed",
            order_id=str(order.id),
            error=str(exc),
        )
        return False
