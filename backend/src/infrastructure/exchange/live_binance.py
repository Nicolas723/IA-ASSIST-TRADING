"""
Live Binance execution adapter.

Thin specialization of :class:`LiveExchangeAdapter` kept for backwards
compatibility with the vault connection test and existing imports.
"""

from __future__ import annotations

from src.domain.market.entities import Exchange
from src.infrastructure.exchange.live_exchange import LiveExchangeAdapter


class LiveBinanceAdapter(LiveExchangeAdapter):
    """Real-money / testnet adapter for Binance Spot."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        is_testnet: bool = True,
        market_type: str = "spot",
    ) -> None:
        super().__init__(
            exchange=Exchange.BINANCE,
            api_key=api_key,
            api_secret=api_secret,
            is_testnet=is_testnet,
            market_type=market_type,
        )
