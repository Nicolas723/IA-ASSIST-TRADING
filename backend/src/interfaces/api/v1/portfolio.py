"""Portfolio API Router — v1"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.application.services.trading_service import TradingService
from src.domain.market.entities import Exchange
from src.domain.trading.entities import ExecutionMode, Portfolio, Position
from src.infrastructure.persistence.repositories.trading_repository import (
    SqlTradingRepository,
)
from src.interfaces.api.v1.deps import get_trading_repository, get_trading_service
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

router = APIRouter()


class ResetAccountBody(BaseModel):
    initial_balance: float = Field(default=10000.0, ge=100.0, le=10000000.0)


@router.get("", response_model=Portfolio, summary="Get full portfolio state")
async def get_portfolio(
    mode: ExecutionMode = Query(ExecutionMode.PAPER),
    service: TradingService = Depends(get_trading_service),
) -> Portfolio:
    """Return comprehensive portfolio equity state, unrealized PnL, cash balance, and open positions."""
    return service.get_portfolio(mode=mode)


@router.get("/positions", response_model=list[Position], summary="Get open positions")
async def get_positions(
    mode: ExecutionMode = Query(ExecutionMode.PAPER),
    service: TradingService = Depends(get_trading_service),
) -> list[Position]:
    """Return active open positions."""
    return service.get_positions(mode=mode)


@router.post("/reset", response_model=Portfolio, summary="Reset paper trading account balance")
async def reset_paper_account(
    body: ResetAccountBody | None = None,
    service: TradingService = Depends(get_trading_service),
    journal: SqlTradingRepository = Depends(get_trading_repository),
) -> Portfolio:
    """Reset the virtual paper trading account and wipe paper order history."""
    init_bal = body.initial_balance if body else 10000.0
    portfolio = service.reset_paper_account(initial_balance=init_bal)

    # Clear the durable journal too, otherwise a restart would rehydrate the
    # account we just reset. Then persist a fresh snapshot so the new starting
    # balance survives restarts.
    try:
        await journal.clear_journal(ExecutionMode.PAPER)
        await journal.save_portfolio_snapshot(portfolio, Exchange.PAPER)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("journal_reset_failed", error=str(exc))

    return portfolio
