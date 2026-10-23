"""
Paper Trading Simulated Exchange Adapter.
Simulates order fills, slippage, maker/taker fees, and maintains real-time paper balances & positions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel

from src.domain.market.entities import Exchange
from src.domain.trading.entities import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    Trade,
)
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class PlaceOrderRequest(BaseModel):
    symbol: str = "BTC/USDT"
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_id: UUID | None = None
    client_order_id: str | None = None
    notes: str = ""


class PaperExchange:
    """
    In-memory virtual execution engine with live balance tracking and PnL calculation.
    """

    def __init__(
        self, initial_balance: float = 10000.0, fee_rate: float = 0.001, slippage: float = 0.0005
    ) -> None:
        self.initial_balance = Decimal(str(initial_balance))
        self.available_balance = self.initial_balance
        self.fee_rate = Decimal(str(fee_rate))
        self.slippage = Decimal(str(slippage))

        self.orders: dict[UUID, Order] = {}
        self.positions: dict[UUID, Position] = {}
        self.trades: list[Trade] = []
        self.realized_pnl = Decimal("0")
        self.total_fees_paid = Decimal("0")
        self.peak_equity = self.initial_balance

    def reset_account(self, initial_balance: float = 10000.0) -> None:
        self.initial_balance = Decimal(str(initial_balance))
        self.available_balance = self.initial_balance
        self.orders.clear()
        self.positions.clear()
        self.trades.clear()
        self.realized_pnl = Decimal("0")
        self.total_fees_paid = Decimal("0")
        self.peak_equity = self.initial_balance
        logger.info("paper_exchange_reset", initial_balance=initial_balance)

    def restore(
        self,
        *,
        positions: list[Position],
        orders: list[Order],
        trades: list[Trade],
        available_balance: Decimal | None = None,
        realized_pnl: Decimal | None = None,
        peak_equity: Decimal | None = None,
    ) -> None:
        """
        Rehydrate the virtual exchange from the durable journal.

        Called on application startup so the paper account resumes exactly where
        it left off (open positions, cash balance, realized PnL, order history).
        Idempotent: each call replaces the in-memory state.
        """
        self.positions = {position.id: position for position in positions}
        self.orders = {order.id: order for order in orders}
        self.trades = list(trades)

        if available_balance is not None:
            self.available_balance = available_balance
        if realized_pnl is not None:
            self.realized_pnl = realized_pnl
        if peak_equity is not None:
            self.peak_equity = peak_equity
        # Never let peak equity fall below current cash (defensive against bad snapshots)
        if self.peak_equity < self.available_balance:
            self.peak_equity = self.available_balance

        self.total_fees_paid = sum((trade.commission for trade in trades), Decimal("0"))

        logger.info(
            "paper_exchange_restored",
            positions=len(self.positions),
            orders=len(self.orders),
            trades=len(self.trades),
            available_balance=float(self.available_balance),
            realized_pnl=float(self.realized_pnl),
        )

    def place_order(self, req: PlaceOrderRequest, current_market_price: float) -> Order:
        """
        Simulate order placement and immediate fill for market orders.
        """
        now = datetime.now(tz=UTC)
        order_id = uuid4()
        mkt_price_dec = Decimal(str(current_market_price))
        qty_dec = Decimal(str(req.quantity))

        # Apply slippage
        if req.side == OrderSide.BUY:
            exec_price = mkt_price_dec * (Decimal("1") + self.slippage)
        else:
            exec_price = mkt_price_dec * (Decimal("1") - self.slippage)

        exec_price = round(exec_price, 2)
        notional_value = qty_dec * exec_price
        fee = notional_value * self.fee_rate

        # Ensure sufficient funds on BUY
        if req.side == OrderSide.BUY:
            total_required = notional_value + fee
            if total_required > self.available_balance:
                order = Order(
                    id=order_id,
                    symbol=req.symbol,
                    exchange=Exchange.PAPER,
                    mode=ExecutionMode.PAPER,
                    side=req.side,
                    order_type=req.order_type,
                    quantity=qty_dec,
                    limit_price=Decimal(str(req.price)) if req.price else None,
                    stop_price=Decimal(str(req.stop_loss)) if req.stop_loss else None,
                    status=OrderStatus.REJECTED,
                    rejection_reason=f"Insufficient funds: Required ${total_required:.2f}, Available ${self.available_balance:.2f}",
                    created_at=now,
                    notes=req.notes,
                )
                self.orders[order_id] = order
                return order

        # Create FILLED order
        order = Order(
            id=order_id,
            symbol=req.symbol,
            exchange=Exchange.PAPER,
            mode=ExecutionMode.PAPER,
            side=req.side,
            order_type=req.order_type,
            quantity=qty_dec,
            filled_quantity=qty_dec,
            average_fill_price=exec_price,
            limit_price=Decimal(str(req.price)) if req.price else None,
            stop_price=Decimal(str(req.stop_loss)) if req.stop_loss else None,
            stop_loss=Decimal(str(req.stop_loss)) if req.stop_loss else None,
            take_profit=Decimal(str(req.take_profit)) if req.take_profit else None,
            commission=fee,
            status=OrderStatus.FILLED,
            strategy_id=req.strategy_id,
            created_at=now,
            submitted_at=now,
            filled_at=now,
            notes=req.notes,
        )
        self.orders[order_id] = order

        # Record trade
        trade = Trade(
            id=uuid4(),
            order_id=order_id,
            symbol=req.symbol,
            exchange=Exchange.PAPER,
            mode=ExecutionMode.PAPER,
            side=req.side,
            quantity=qty_dec,
            price=exec_price,
            commission=fee,
            commission_asset="USDT",
            strategy_id=req.strategy_id,
            executed_at=now,
        )
        self.trades.append(trade)
        self.total_fees_paid += fee

        # Update balance and open/close positions
        if req.side == OrderSide.BUY:
            self.available_balance -= notional_value + fee

            # Check if there is an existing position for this symbol to add to or create new
            pos_id = uuid4()
            position = Position(
                id=pos_id,
                symbol=req.symbol,
                exchange=Exchange.PAPER,
                mode=ExecutionMode.PAPER,
                side=PositionSide.LONG,
                entry_price=exec_price,
                current_price=exec_price,
                quantity=qty_dec,
                stop_loss=Decimal(str(req.stop_loss)) if req.stop_loss else None,
                take_profit=Decimal(str(req.take_profit)) if req.take_profit else None,
                strategy_id=req.strategy_id,
                opened_at=now,
                updated_at=now,
            )
            self.positions[pos_id] = position

        elif req.side == OrderSide.SELL:
            # Close existing position matching symbol if any
            matching_pos_key = next(
                (k for k, p in self.positions.items() if p.symbol == req.symbol), None
            )
            if matching_pos_key:
                pos = self.positions.pop(matching_pos_key)
                pnl = (exec_price - pos.entry_price) * pos.quantity - fee
                self.realized_pnl += pnl
                self.available_balance += pos.quantity * exec_price - fee
            else:
                # Flat sell proceeds
                self.available_balance += notional_value - fee

        logger.info(
            "paper_order_filled",
            order_id=str(order_id),
            symbol=req.symbol,
            side=req.side.value,
            quantity=float(qty_dec),
            price=float(exec_price),
        )
        return order

    def close_position(self, position_id: UUID, current_market_price: float) -> Order | None:
        """Close an open position at current market price."""
        if position_id not in self.positions:
            return None

        pos = self.positions[position_id]
        sell_order_req = PlaceOrderRequest(
            symbol=pos.symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=float(pos.quantity),
            notes=f"Manual Close of Position {str(position_id)[:8]}",
        )
        return self.place_order(sell_order_req, current_market_price)

    def update_market_prices(self, price_map: dict[str, float]) -> None:
        """Update unrealized PnL of all open positions with latest tick prices."""
        now = datetime.now(tz=UTC)
        for pos in self.positions.values():
            if pos.symbol in price_map:
                new_price = Decimal(str(price_map[pos.symbol]))
                pos.current_price = new_price
                pos.updated_at = now

    def get_portfolio(self) -> Portfolio:
        """Calculate and return full portfolio equity state."""
        positions_list = list(self.positions.values())
        unrealized_pnl = sum((p.unrealized_pnl for p in positions_list), Decimal("0"))
        positions_notional = sum((p.notional_value for p in positions_list), Decimal("0"))
        total_equity = self.available_balance + positions_notional + unrealized_pnl

        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        return Portfolio(
            id=uuid4(),
            mode=ExecutionMode.PAPER,
            exchange=Exchange.PAPER,
            total_balance=total_equity,
            available_balance=self.available_balance,
            locked_balance=positions_notional,
            base_currency="USDT",
            positions=positions_list,
            total_realized_pnl=self.realized_pnl,
            peak_value=self.peak_equity,
            updated_at=datetime.now(tz=UTC),
        )
