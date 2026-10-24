"""
Bybit public market-data adapter using CCXT.

Only public data is exposed here (OHLCV, ticker, order book). Execution stays
on the Binance live path; this adapter exists so the assistant can ingest a
second exchange without implying that trading is supported there.
"""

from __future__ import annotations

import ccxt.async_support as ccxt

from src.domain.market.entities import Exchange
from src.infrastructure.exchange.ccxt_base import CcxtBaseAdapter


class BybitAdapter(CcxtBaseAdapter):
    """Bybit public market-data implementation (no credentials required)."""

    def __init__(self) -> None:
        client = ccxt.bybit(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        super().__init__(exchange_id=Exchange.BYBIT, ccxt_exchange=client)
