"""
Market domain entities.

These are pure Python objects — no ORM, no framework dependencies.
They represent the core concepts of market data as understood by the business.

Design decisions:
- Immutable (frozen=True) where possible to prevent accidental mutation
- Decimal for all financial values (no float precision bugs)
- Strict validation via Pydantic v2
- Enums for categorical values (Timeframe, Exchange)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from src.shared.types.common import Price, Volume
from src.shared.types.common import SymbolStr as SymbolStr

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Timeframe(StrEnum):
    """Supported candlestick timeframes."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"

    @property
    def seconds(self) -> int:
        """Duration of this timeframe in seconds."""
        mapping = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "6h": 21600,
            "8h": 28800,
            "12h": 43200,
            "1d": 86400,
            "1w": 604800,
            "1M": 2592000,
        }
        return mapping[self.value]


class Exchange(StrEnum):
    """Supported exchanges."""

    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    KRAKEN = "kraken"
    COINBASE = "coinbase"
    PAPER = "paper"  # Paper trading virtual exchange
    BACKTEST = "backtest"  # Backtest simulation


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"
    PERPETUAL = "perpetual"
    OPTIONS = "options"


# ---------------------------------------------------------------------------
# Value Objects
# ---------------------------------------------------------------------------


class Symbol(BaseModel):
    """
    A trading pair symbol.

    Examples: BTC/USDT, ETH/BTC, AAPL/USD
    """

    model_config = {"frozen": True}

    raw: SymbolStr = Field(description="Raw symbol string e.g. BTC/USDT")
    exchange: Exchange
    market_type: MarketType = MarketType.SPOT

    @field_validator("raw")
    @classmethod
    def validate_symbol_format(cls, v: str) -> str:
        v = v.upper().strip()
        if "/" not in v:
            msg = f"Symbol must be in BASE/QUOTE format, got: {v!r}"
            raise ValueError(msg)
        return v

    @property
    def base(self) -> str:
        return self.raw.split("/")[0]

    @property
    def quote(self) -> str:
        return self.raw.split("/")[1]

    def __str__(self) -> str:
        return self.raw


class OHLCV(BaseModel):
    """Open/High/Low/Close/Volume values for a candle."""

    model_config = {"frozen": True}

    open: Price
    high: Price
    low: Price
    close: Price
    volume: Volume

    @model_validator(mode="after")
    def validate_ohlcv_integrity(self) -> OHLCV:
        if self.high < self.low:
            msg = f"High ({self.high}) cannot be less than Low ({self.low})"
            raise ValueError(msg)
        if self.high < self.open or self.high < self.close:
            msg = "High must be >= Open and Close"
            raise ValueError(msg)
        if self.low > self.open or self.low > self.close:
            msg = "Low must be <= Open and Close"
            raise ValueError(msg)
        if self.volume < Decimal("0"):
            msg = "Volume cannot be negative"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class Candle(BaseModel):
    """
    A single OHLCV candlestick.

    Immutable entity — once created from exchange data, it should not change.
    Represents the definitive record of price action in a given period.
    """

    model_config = {"frozen": True}

    id: UUID = Field(default_factory=uuid4)
    symbol: SymbolStr
    exchange: Exchange
    timeframe: Timeframe
    timestamp: datetime  # Opening time of the candle (UTC)
    ohlcv: OHLCV
    is_closed: bool = True  # False if this is the current (live) candle
    trades_count: int | None = None  # Number of trades in this candle

    # Convenience accessors
    @property
    def open(self) -> Price:
        return self.ohlcv.open

    @property
    def high(self) -> Price:
        return self.ohlcv.high

    @property
    def low(self) -> Price:
        return self.ohlcv.low

    @property
    def close(self) -> Price:
        return self.ohlcv.close

    @property
    def volume(self) -> Volume:
        return self.ohlcv.volume

    @property
    def body_size(self) -> Price:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def upper_wick(self) -> Price:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> Price:
        return min(self.open, self.close) - self.low


class Tick(BaseModel):
    """
    A real-time price tick from an exchange.

    Represents a single trade or best bid/ask snapshot.
    """

    model_config = {"frozen": True}

    symbol: SymbolStr
    exchange: Exchange
    timestamp: datetime
    price: Price
    volume: Volume
    side: str | None = None  # "buy" | "sell" | None (for quotes)


class OrderBookLevel(BaseModel):
    """A single price level in an order book."""

    model_config = {"frozen": True}

    price: Price
    quantity: Volume


class OrderBook(BaseModel):
    """
    Exchange order book snapshot.

    Represents the current state of bids and asks at a point in time.
    """

    model_config = {"frozen": True}

    symbol: SymbolStr
    exchange: Exchange
    timestamp: datetime
    bids: list[OrderBookLevel]  # Sorted descending by price
    asks: list[OrderBookLevel]  # Sorted ascending by price

    @property
    def best_bid(self) -> Price | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Price | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Price | None:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Price | None:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / Decimal("2")
        return None


class MarketContext(BaseModel):
    """
    Aggregated market context provided to AI agents for analysis.

    This is the primary input to the agent system — a snapshot of
    everything the agents need to make an informed analysis.
    """

    symbol: SymbolStr
    exchange: Exchange
    timestamp: datetime

    # Multi-timeframe candles: {timeframe: list[Candle]}
    candles: dict[str, list[Candle]]

    # Latest tick
    last_tick: Tick | None = None

    # Order book snapshot
    order_book: OrderBook | None = None

    # Pre-calculated indicators: {indicator_name: value or dict}
    indicators: dict[str, object] = Field(default_factory=dict)

    # Data quality
    data_quality_score: float = Field(default=1.0, ge=0.0, le=1.0)
    missing_data_warnings: list[str] = Field(default_factory=list)
