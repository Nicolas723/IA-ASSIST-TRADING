"""Trading API Router — v1"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.application.services.journal import (
    persist_live_positions_to_database,
    record_execution,
)
from src.application.services.live_trading_service import LiveTradingService, LiveTradingStatus
from src.application.services.market_data_service import MarketDataService
from src.application.services.runtime_config_service import (
    RuntimeConfigService,
    RuntimeTradingConfigResponse,
    RuntimeTradingConfigUpdate,
)
from src.application.services.trading_service import TradingService
from src.domain.market.entities import Exchange, Timeframe
from src.domain.trading.entities import ExecutionMode, Order, OrderSide, OrderType, Position
from src.infrastructure.exchange.paper import PlaceOrderRequest
from src.infrastructure.persistence.repositories.trading_repository import (
    SqlTradingRepository,
)
from src.interfaces.api.v1.deps import (
    get_live_trading_service,
    get_market_data_service,
    get_runtime_config_service,
    get_trading_repository,
    get_trading_service,
)
from src.shared.exceptions.domain import (
    ExchangeError,
    RiskViolationError,
    SecurityError,
    ValidationError,
)

router = APIRouter()


class SubmitOrderBody(BaseModel):
    symbol: str = "BTC/USDT"
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float = Field(..., gt=0)
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    mode: ExecutionMode = ExecutionMode.PAPER
    notes: str = ""


class ClosePositionBody(BaseModel):
    current_price: float | None = None


class RealOrderBody(BaseModel):
    symbol: str = "BTC/USDT"
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float = Field(..., gt=0)
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    exchange: Exchange = Exchange.BINANCE
    confirm: bool = False
    client_order_id: str | None = Field(default=None, max_length=36)
    notes: str = ""


class RealCloseBody(BaseModel):
    position_id: UUID | None = None
    symbol: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    current_price: float | None = None
    exchange: Exchange | None = None
    confirm: bool = False


class RealAccountResponse(BaseModel):
    exchange: str
    testnet: bool
    balances: dict[str, float]


def _real_error_status(exc: Exception) -> int:
    if isinstance(exc, SecurityError):
        return 403
    if isinstance(exc, RiskViolationError):
        return 422
    if isinstance(exc, ExchangeError):
        return 502
    if isinstance(exc, (ValidationError, ValueError)):
        return 400
    return 500


async def _resolve_price(
    market_service: MarketDataService,
    symbol: str,
    provided: float | None,
) -> float:
    if provided is not None and provided > 0:
        return provided
    try:
        latest = float(await market_service.get_latest_price(symbol))
        if latest > 0:
            return latest
    except Exception:
        pass
    candles = await market_service.get_candles(symbol=symbol, timeframe=Timeframe.M1, limit=5)
    if not candles:
        candles = await market_service.get_candles(symbol=symbol, timeframe=Timeframe.H1, limit=5)
    if not candles:
        raise HTTPException(
            status_code=400,
            detail=f"Could not determine market price for {symbol}. Please specify price.",
        )
    return float(candles[-1].close)


@router.get("/orders", response_model=list[Order], summary="List orders")
async def list_orders(
    mode: ExecutionMode = Query(ExecutionMode.PAPER),
    service: TradingService = Depends(get_trading_service),
) -> list[Order]:
    """Retrieve all open and executed orders."""
    return service.get_orders(mode=mode)


@router.get(
    "/orders/history",
    response_model=list[Order],
    summary="List durable order history (survives restarts)",
)
async def list_order_history(
    mode: ExecutionMode | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    journal: SqlTradingRepository = Depends(get_trading_repository),
) -> list[Order]:
    """Read the persisted trading journal (orders that outlive the process)."""
    return await journal.list_orders(mode=mode, symbol=symbol, limit=limit)


@router.post("/orders", response_model=Order, summary="Place a risk-guarded order")
async def place_order(
    body: SubmitOrderBody,
    trading_service: TradingService = Depends(get_trading_service),
    market_service: MarketDataService = Depends(get_market_data_service),
    journal: SqlTradingRepository = Depends(get_trading_repository),
) -> Order:
    """
    Execute order across paper or real trading environments with mandatory pre-flight risk checks.
    """
    normalized_symbol = body.symbol.replace("-", "/").replace("_", "/").upper()

    # Determine current market price
    current_price = body.price
    if current_price is None or current_price <= 0:
        candles = await market_service.get_candles(
            symbol=normalized_symbol, timeframe=Timeframe.M1, limit=5
        )
        if not candles:
            # Fallback to 1h
            candles = await market_service.get_candles(
                symbol=normalized_symbol, timeframe=Timeframe.H1, limit=5
            )
        if candles:
            current_price = float(candles[-1].close)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Could not determine market price for {normalized_symbol}. Please specify price or sync market data.",
            )

    req = PlaceOrderRequest(
        symbol=normalized_symbol,
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        price=current_price,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        notes=body.notes,
    )

    try:
        order = trading_service.place_order(
            req=req, current_market_price=current_price, mode=body.mode
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await record_execution(journal, trading_service, order, body.mode)
    return order


@router.post(
    "/positions/{position_id}/close", response_model=Order, summary="Close an open position"
)
async def close_position(
    position_id: UUID,
    body: ClosePositionBody | None = None,
    trading_service: TradingService = Depends(get_trading_service),
    market_service: MarketDataService = Depends(get_market_data_service),
    journal: SqlTradingRepository = Depends(get_trading_repository),
) -> Order:
    """Close an open position at market price."""
    positions = trading_service.get_positions(ExecutionMode.PAPER)
    pos = next((p for p in positions if p.id == position_id), None)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Position '{position_id}' not found")

    current_price = body.current_price if body and body.current_price else None
    if not current_price:
        candles = await market_service.get_candles(
            symbol=pos.symbol, timeframe=Timeframe.M1, limit=5
        )
        current_price = float(candles[-1].close) if candles else float(pos.current_price)

    order = trading_service.close_position(
        position_id=position_id, current_market_price=current_price
    )
    if not order:
        raise HTTPException(status_code=400, detail="Failed to close position")

    await record_execution(journal, trading_service, order, ExecutionMode.PAPER)
    return order


# ---------------------------------------------------------------------------
# Real trading (Binance / Bybit) — manual, guarded execution
# ---------------------------------------------------------------------------


@router.get(
    "/real/status",
    response_model=LiveTradingStatus,
    summary="Get real-trading readiness and safety interlocks",
)
async def get_real_trading_status(
    service: LiveTradingService = Depends(get_live_trading_service),
) -> LiveTradingStatus:
    """Expose whether real trading is enabled and which safeguards are active."""
    return service.status()


@router.get(
    "/runtime-config",
    response_model=RuntimeTradingConfigResponse,
    summary="Get the UI-managed trading configuration",
)
async def get_runtime_trading_config(
    service: RuntimeConfigService = Depends(get_runtime_config_service),
) -> RuntimeTradingConfigResponse:
    """Return effective execution flags, limits and environment readiness."""
    return service.snapshot()


@router.put(
    "/runtime-config",
    response_model=RuntimeTradingConfigResponse,
    summary="Update trading mode, environment and safety limits from the app",
)
async def update_runtime_trading_config(
    body: RuntimeTradingConfigUpdate,
    service: RuntimeConfigService = Depends(get_runtime_config_service),
    live_service: LiveTradingService = Depends(get_live_trading_service),
) -> RuntimeTradingConfigResponse:
    """
    Persist and apply runtime trading changes.

    Paper/real and testnet/mainnet are independent switches. MAINNET still
    requires ``allow_mainnet_trading`` and ``confirm=true``. Changing the
    environment is blocked while real positions are open to avoid mixing
    balances from two different venues.
    """
    current = service.current()
    if (
        body.trading_environment is not None
        and body.trading_environment != current.trading_environment
        and live_service.get_positions(ExecutionMode.REAL)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Cierra o reconcilia las posiciones reales antes de cambiar entre "
                "testnet y mainnet."
            ),
        )
    try:
        service.update(body)
    except Exception as exc:
        raise HTTPException(status_code=_real_error_status(exc), detail=str(exc)) from exc
    return service.snapshot()


@router.get(
    "/real/account",
    response_model=RealAccountResponse,
    summary="Get live exchange balances",
)
async def get_real_account(
    exchange: Exchange = Query(Exchange.BINANCE),
    service: LiveTradingService = Depends(get_live_trading_service),
) -> RealAccountResponse:
    """Query the exchange account (requires configured vault credentials)."""
    try:
        balances = await service.account_balances(exchange)
    except Exception as exc:
        raise HTTPException(status_code=_real_error_status(exc), detail=str(exc)) from exc
    return RealAccountResponse(
        exchange=exchange.value,
        testnet=service.status(exchange).testnet,
        balances=balances,
    )


@router.get(
    "/real/positions",
    response_model=list[Position],
    summary="List positions tracked for real trading",
)
async def list_real_positions(
    service: LiveTradingService = Depends(get_live_trading_service),
) -> list[Position]:
    """Return the in-memory ledger of real positions opened by this process."""
    return service.get_positions(ExecutionMode.REAL)


@router.post(
    "/real/reconcile",
    summary="Reconcile tracked real positions with exchange balances",
)
async def reconcile_real_positions(
    service: LiveTradingService = Depends(get_live_trading_service),
) -> dict[str, object]:
    """Drop or resize local positions that no longer match the exchange balance."""
    before = {
        position.id: position.quantity for position in service.get_positions(ExecutionMode.REAL)
    }
    removed = await service.reconcile_positions()
    after = {
        position.id: position.quantity for position in service.get_positions(ExecutionMode.REAL)
    }
    if removed or before != after:
        await persist_live_positions_to_database(service)
    return {
        "removed": removed,
        "open_positions": len(service.get_positions(ExecutionMode.REAL)),
        "reconciliation_interval_seconds": (
            service.settings.real_trading_reconcile_interval_seconds
        ),
    }


@router.post(
    "/real/orders",
    response_model=Order,
    summary="Place a guarded real order (requires confirm=true)",
)
async def place_real_order(
    body: RealOrderBody,
    live_service: LiveTradingService = Depends(get_live_trading_service),
    market_service: MarketDataService = Depends(get_market_data_service),
    journal: SqlTradingRepository = Depends(get_trading_repository),
) -> Order:
    """
    Execute a real order after all safety checks pass.

    The request must include ``confirm=true`` when REQUIRE_MANUAL_CONFIRMATION
    is enabled. MAINNET orders additionally require ALLOW_MAINNET_TRADING=true.
    """
    normalized_symbol = body.symbol.replace("-", "/").replace("_", "/").upper()
    current_price = await _resolve_price(market_service, normalized_symbol, body.price)

    req = PlaceOrderRequest(
        symbol=normalized_symbol,
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        price=current_price,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        client_order_id=body.client_order_id,
        notes=body.notes,
    )

    try:
        order = await live_service.place_order(
            req=req,
            current_market_price=current_price,
            confirm=body.confirm,
            exchange=body.exchange,
        )
    except Exception as exc:
        raise HTTPException(status_code=_real_error_status(exc), detail=str(exc)) from exc

    await record_execution(journal, live_service, order, ExecutionMode.REAL, order.exchange)
    return order


@router.post(
    "/real/positions/close",
    response_model=Order,
    summary="Close a real position (by id or symbol)",
)
async def close_real_position(
    body: RealCloseBody,
    live_service: LiveTradingService = Depends(get_live_trading_service),
    market_service: MarketDataService = Depends(get_market_data_service),
    journal: SqlTradingRepository = Depends(get_trading_repository),
) -> Order:
    """Close a tracked position by id, or sell free base balance for a symbol."""
    if body.position_id is None and not body.symbol:
        raise HTTPException(status_code=400, detail="Provide position_id or symbol")

    try:
        if body.position_id is not None:
            position = next(
                (
                    pos
                    for pos in live_service.get_positions(ExecutionMode.REAL)
                    if pos.id == body.position_id
                ),
                None,
            )
            if position is None:
                raise HTTPException(
                    status_code=404, detail=f"Real position '{body.position_id}' not found"
                )
            price = body.current_price or float(position.current_price)
            if price <= 0:
                price = await _resolve_price(market_service, position.symbol, None)
            order = await live_service.close_position(
                position_id=body.position_id,
                current_market_price=price,
                confirm=body.confirm,
                exchange=body.exchange,
            )
        else:
            assert body.symbol is not None
            normalized_symbol = body.symbol.replace("-", "/").replace("_", "/").upper()
            price = await _resolve_price(market_service, normalized_symbol, body.current_price)
            order = await live_service.close_symbol(
                symbol=normalized_symbol,
                quantity=body.quantity,
                current_market_price=price,
                confirm=body.confirm,
                exchange=body.exchange,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=_real_error_status(exc), detail=str(exc)) from exc

    if order is None:
        raise HTTPException(status_code=400, detail="Failed to close real position")
    await record_execution(journal, live_service, order, ExecutionMode.REAL, order.exchange)
    return order
