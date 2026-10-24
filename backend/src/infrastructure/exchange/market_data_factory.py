"""
Market-data adapter selection.

The runtime can ingest public candles from Binance or Bybit. Execution remains
Binance-only and is gated by its own flags; choosing Bybit here never enables
orders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.infrastructure.exchange.binance import BinanceAdapter
from src.infrastructure.exchange.bybit import BybitAdapter

if TYPE_CHECKING:
    from src.infrastructure.exchange.ccxt_base import CcxtBaseAdapter


def create_market_data_adapter(exchange: str) -> CcxtBaseAdapter:
    """Build a public market-data adapter from a configured exchange name."""
    normalized = exchange.strip().lower()
    if normalized == "binance":
        return BinanceAdapter()
    if normalized == "bybit":
        return BybitAdapter()
    raise ValueError(
        f"Unsupported market-data exchange '{exchange}'. Supported values: binance, bybit."
    )
