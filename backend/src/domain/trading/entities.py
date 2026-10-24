"""
Trading domain entities.

Core business objects for order management, position tracking, and portfolio.

Design decisions:
- ExecutionMode is a first-class entity (not a flag) — drives behavior throughout
- Orders are immutable snapshots; state transitions create new objects
- Position tracks unrealized PnL in real time
- Portfolio aggregates all positions + available balance
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from src.domain.market.entities import Exchange
from src.shared.types.common import Price, SymbolStr, Volume

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExecutionMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    REAL = "real"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"


class OrderStatus(StrEnum):
    PENDING = "pending"  # Created locally, not yet sent
    OPEN = "open"  # Sent to exchange, awaiting fill
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"  # Fully executed
    CANCELLED = "cancelled"  # Cancelled by user or system
    REJECTED = "rejected"  # Rejected by exchange
    EXPIRED = "expired"  # Time in force expired


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class TradeOutcome(StrEnum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"
    OPEN = "open"  # Position not yet closed


# ---------------------------------------------------------------------------
# Value Objects
# ---------------------------------------------------------------------------


class OrderId(BaseModel):
    """Typed order identifier."""

    model_config = {"frozen": True}

    local_id: UUID = Field(default_factory=uuid4)
    exchange_id: str | None = None  # Assigned by exchange after submission


# ---------------------------------------------------------------------------
# Core Entities
# ---------------------------------------------------------------------------


class Order(BaseModel):
    """
    A trading order in any execution mode.

    Represents the intent to buy or sell at specific conditions.
    Orders are created in PENDING state and transition through states
    as they are submitted, filled, or cancelled.

    This entity is mode-agnostic — the execution layer determines
    whether it's simulated (paper/backtest) or real.
    """

    id: UUID = Field(default_factory=uuid4)
    symbol: SymbolStr
    exchange: Exchange
    mode: ExecutionMode

    side: OrderSide
    order_type: OrderType
    quantity: Volume

    # Price fields (None for market orders)
    limit_price: Price | None = None
    stop_price: Price | None = None

    # Status tracking
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Volume = Field(default=Decimal("0"))
    average_fill_price: Price | None = None
    commission: Decimal = Field(default=Decimal("0"))
    commission_asset: str = "USDT"

    # Risk management (pre-validated)
    stop_loss: Price | None = None
    take_profit: Price | None = None

    # Metadata
    strategy_id: UUID | None = None
    signal_id: UUID | None = None
    coordinator_decision_id: UUID | None = None
    client_order_id: str | None = None  # Used for dedup

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    cancelled_at: datetime | None = None

    # Notes
    rejection_reason: str | None = None
    notes: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remaining_quantity(self) -> Volume:
        return self.quantity - self.filled_quantity

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_complete(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @model_validator(mode="after")
    def validate_limit_price(self) -> Order:
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            msg = f"limit_price is required for {self.order_type} orders"
            raise ValueError(msg)
        if (
            self.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT)
            and self.stop_price is None
        ):
            msg = f"stop_price is required for {self.order_type} orders"
            raise ValueError(msg)
        return self


class Trade(BaseModel):
    """
    A completed execution — an order that has been filled.

    Represents the actual transaction that occurred, with fill details.
    """

    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    symbol: SymbolStr
    exchange: Exchange
    mode: ExecutionMode

    side: OrderSide
    quantity: Volume
    price: Price
    commission: Decimal
    commission_asset: str

    strategy_id: UUID | None = None
    coordinator_decision_id: UUID | None = None
    executed_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def notional_value(self) -> Decimal:
        return self.price * self.quantity


class Position(BaseModel):
    """
    An open trading position.

    Represents the current state of a holding, including real-time PnL.
    Updated as new fills come in or price changes are applied.
    """

    id: UUID = Field(default_factory=uuid4)
    symbol: SymbolStr
    exchange: Exchange
    mode: ExecutionMode
    side: PositionSide

    entry_price: Price
    quantity: Volume
    current_price: Price = Field(default=Decimal("0"))

    realized_pnl: Decimal = Field(default=Decimal("0"))
    total_commission: Decimal = Field(default=Decimal("0"))

    strategy_id: UUID | None = None
    coordinator_decision_id: UUID | None = None
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Risk levels
    stop_loss: Price | None = None
    take_profit: Price | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def notional_value(self) -> Decimal:
        return self.entry_price * self.quantity

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl(self) -> Decimal:
        if self.current_price == Decimal("0"):
            return Decimal("0")
        if self.side == PositionSide.LONG:
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl_pct(self) -> Decimal:
        if self.notional_value == Decimal("0"):
            return Decimal("0")
        return (self.unrealized_pnl / self.notional_value) * Decimal("100")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pnl(self) -> Decimal:
        return self.unrealized_pnl + self.realized_pnl - self.total_commission


class Portfolio(BaseModel):
    """
    Portfolio state at a point in time.

    Aggregates all open positions and available balance.
    """

    id: UUID = Field(default_factory=uuid4)
    mode: ExecutionMode
    exchange: Exchange

    # Balances
    total_balance: Decimal  # Total equity (cash + positions)
    available_balance: Decimal  # Cash not in positions
    locked_balance: Decimal  # Margin / reserved for open orders
    base_currency: str = "USDT"

    # Positions
    positions: list[Position] = Field(default_factory=list)

    # Performance
    total_realized_pnl: Decimal = Field(default=Decimal("0"))
    daily_pnl: Decimal = Field(default=Decimal("0"))
    peak_value: Decimal = Field(default=Decimal("0"))

    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_unrealized_pnl(self) -> Decimal:
        return sum((p.unrealized_pnl for p in self.positions), Decimal("0"))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def current_drawdown_pct(self) -> Decimal:
        if self.peak_value == Decimal("0"):
            return Decimal("0")
        return ((self.peak_value - self.total_balance) / self.peak_value) * Decimal("100")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def position_count(self) -> int:
        return len(self.positions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gross_exposure_usd(self) -> Decimal:
        """Mark-to-market notional of every open position."""
        return self.exposure_usd()

    def exposure_usd(self, symbol: str | None = None) -> Decimal:
        """Notional exposure, optionally filtered to a single symbol.

        Uses the last known price when available so exposure reflects current
        market value instead of the original entry notional.
        """
        total = Decimal("0")
        for position in self.positions:
            if symbol is not None and position.symbol != symbol:
                continue
            price = (
                position.current_price
                if position.current_price > Decimal("0")
                else position.entry_price
            )
            total += price * position.quantity
        return total
