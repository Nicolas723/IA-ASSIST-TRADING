"""
Binance Adapter using CCXT.
"""

from __future__ import annotations

import ccxt.async_support as ccxt

from src.domain.market.entities import Exchange
from src.infrastructure.exchange.ccxt_base import CcxtBaseAdapter
from src.shared.config.settings import get_settings


class BinanceAdapter(CcxtBaseAdapter):
    """Binance specific implementation."""

    def __init__(self) -> None:
        settings = get_settings()

        # Configure Binance client
        client_config = {
            "apiKey": getattr(settings, "binance_api_key", ""),
            "secret": getattr(settings, "binance_secret", ""),
            "enableRateLimit": True,
        }

        # We need to decrypt keys here in a real scenario if fetched from DB.
        # But for Phase 1 testing, we can use the env vars if set.
        client = ccxt.binance(client_config)

        # Optional: set testnet
        if getattr(settings, "binance_testnet", False):
            client.set_sandbox_mode(True)

        super().__init__(exchange_id=Exchange.BINANCE, ccxt_exchange=client)
