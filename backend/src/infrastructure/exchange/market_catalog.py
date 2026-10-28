"""Suggested spot markets ranked by live Binance volume.

Only well-known, liquid crypto assets are suggested: Binance also lists
stablecoin pairs and tokenized stocks, which make poor candidates for a
trend-following committee. The 24h figures come from the public mainnet API
(the real market); ``tradable`` says whether the pair exists on the network
orders go to, so a Testnet session never picks a market it cannot trade.
"""

from __future__ import annotations

import asyncio

import httpx
from pydantic import BaseModel

from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

MAINNET_API = "https://api.binance.com/api/v3"
TESTNET_API = "https://testnet.binance.vision/api/v3"

# Liquid, long-lived assets. Order does not matter: results are sorted by volume.
CURATED_BASES = (
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "LINK", "AVAX",
    "SUI", "LTC", "DOT", "NEAR", "UNI", "AAVE", "BCH", "XLM", "HBAR", "APT",
    "ARB", "OP", "FET", "TAO", "WLD", "ENA", "INJ", "ATOM", "ETC", "FIL",
    "PEPE", "SHIB", "TON", "ICP", "RENDER", "SEI", "TIA", "ONDO", "JUP", "BONK",
)  # fmt: skip


class MarketOption(BaseModel):
    symbol: str
    volume_usd_24h: float
    change_pct_24h: float
    tradable: bool


async def fetch_market_catalog(
    *,
    testnet: bool,
    quote: str = "USDT",
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[MarketOption]:
    """Curated markets with their real 24h volume, most liquid first."""
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        tickers_request = client.get(f"{MAINNET_API}/ticker/24hr", params={"type": "MINI"})
        if testnet:
            info_request = client.get(f"{TESTNET_API}/exchangeInfo")
            tickers, info = await asyncio.gather(tickers_request, info_request)
        else:
            tickers, info = await tickers_request, None
    tickers.raise_for_status()

    available: set[str] | None = None
    if info is not None:
        try:
            info.raise_for_status()
            available = {
                str(row["symbol"])
                for row in info.json().get("symbols", [])
                if row.get("status") == "TRADING"
            }
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Unknown testnet coverage: still suggest, but do not claim tradability.
            logger.warning("market_catalog_testnet_unavailable", error=str(exc))
            available = set()

    wanted = {f"{base}{quote}": base for base in CURATED_BASES}
    options: list[MarketOption] = []
    for row in tickers.json():
        pair = str(row.get("symbol") or "")
        base = wanted.get(pair)
        if base is None:
            continue
        try:
            volume = float(row.get("quoteVolume") or 0)
            open_price = float(row.get("openPrice") or 0)
            last = float(row.get("lastPrice") or 0)
        except (TypeError, ValueError):
            continue
        change = (last - open_price) / open_price * 100 if open_price > 0 else 0.0
        options.append(
            MarketOption(
                symbol=f"{base}/{quote}",
                volume_usd_24h=round(volume, 2),
                change_pct_24h=round(change, 2),
                tradable=available is None or pair in available,
            )
        )
    options.sort(key=lambda option: option.volume_usd_24h, reverse=True)
    return options
