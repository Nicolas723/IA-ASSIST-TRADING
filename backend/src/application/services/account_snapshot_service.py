"""Real account balance and account-level P&L snapshot.

The exchange is the source of truth for balances. This service turns the raw
per-asset totals into a USDT equity estimate, adds the bot's realized and
unrealized P&L, and compares the result against a persisted reference so the
operator can see gains or losses without reconstructing deposits by hand.

The reference is intentionally explicit: it is not a tax or accounting basis,
only "equity when the reference was set". The UI exposes a button to reset it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.domain.trading.entities import ExecutionMode
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

# Assets treated as 1:1 with the quote currency. Anything else is priced through
# the public market-data feed.
_STABLE_ASSETS = {"USDT", "USDC", "FDUSD", "BUSD", "DAI", "TUSD", "USDP"}

_DEFAULT_BASELINE_FILE = Path(__file__).resolve().parents[3] / "vault" / ".account_baseline.json"

# Bound concurrent public ticker requests so a large balance list cannot trip
# the exchange rate limiter.
_MAX_CONCURRENT_PRICE_LOOKUPS = 8
_UNPRICED_RETRY_SECONDS = 900.0


def _pricing_asset(asset: str) -> str:
    """Asset whose price values ``asset``.

    Binance reports Simple Earn deposits as ``LD`` + asset (``LDUSDT``,
    ``LDBTC``). They are the same coin, just not available for spot trading.
    """
    if asset.startswith("LD") and len(asset) > 3:
        return asset[2:]
    return asset


PriceLookup = Callable[[str], Awaitable[float | None]]
# Returns prices for the symbols that have a market; raises on a failed request.
BatchPriceLookup = Callable[[list[str]], Awaitable[dict[str, float]]]


class AssetBalance(BaseModel):
    asset: str
    total: float
    free: float
    used: float
    value_usdt: float | None = None


class AccountSnapshot(BaseModel):
    exchange: str
    environment: str
    testnet: bool
    quote_asset: str = "USDT"
    cash_usdt: float
    free_usdt: float
    locked_usdt: float
    equity_usdt: float
    balances: list[AssetBalance] = Field(default_factory=list)
    priced_assets: list[str] = Field(default_factory=list)
    unpriced_assets: list[str] = Field(default_factory=list)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    baseline_equity: float | None = None
    baseline_at: datetime | None = None
    change_usdt: float | None = None
    change_pct: float | None = None
    as_of: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    note: str = (
        "El saldo lo reporta el exchange. La variación se mide contra la "
        "referencia guardada, no contra tus depósitos históricos."
    )


class AccountSnapshotService:
    """Build a USDT-denominated account snapshot from live exchange balances."""

    def __init__(
        self,
        *,
        live_service: Any,
        price_lookup: PriceLookup,
        batch_price_lookup: BatchPriceLookup | None = None,
        baseline_path: Path | None = None,
    ) -> None:
        self._live = live_service
        self._price_lookup = price_lookup
        self._batch_price_lookup = batch_price_lookup
        configured = os.getenv("ACCOUNT_BASELINE_PATH", "").strip()
        self._baseline_file = baseline_path or (
            Path(configured) if configured else _DEFAULT_BASELINE_FILE
        )
        self._memory: dict[str, dict[str, Any]] = {}
        self._unpriced_until: dict[str, float] = {}
        self._price_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PRICE_LOOKUPS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def snapshot(self, exchange: Any = None) -> AccountSnapshot:
        details = await self._live.account_details(exchange)
        status = self._live.status(exchange)
        environment = "testnet" if status.testnet else "mainnet"

        balances: list[AssetBalance] = []
        priced: list[str] = []
        unpriced: list[str] = []
        equity = 0.0

        to_price = sorted(
            {_pricing_asset(asset) for asset in details} - _STABLE_ASSETS,
        )
        prices, pricing_degraded = await self._price_assets(to_price)

        for asset, values in sorted(details.items()):
            total = float(values.get("total", 0.0))
            free = float(values.get("free", 0.0))
            used = float(values.get("used", 0.0))
            base = _pricing_asset(asset)
            value: float | None
            if base in _STABLE_ASSETS:
                value = total
                priced.append(asset)
            else:
                price = prices.get(base)
                if price is None or price <= 0:
                    value = None
                    unpriced.append(asset)
                else:
                    value = total * price
                    priced.append(asset)
            if value is not None:
                equity += value
            balances.append(
                AssetBalance(
                    asset=asset,
                    total=total,
                    free=free,
                    used=used,
                    value_usdt=round(value, 8) if value is not None else None,
                )
            )

        portfolio = self._live.get_portfolio(ExecutionMode.REAL)
        realized = float(portfolio.total_realized_pnl)
        unrealized = float(sum(position.unrealized_pnl for position in portfolio.positions))

        baseline = self._load_baseline(environment)
        now = datetime.now(tz=UTC)
        if baseline is None:
            baseline = {"equity": equity, "at": now.isoformat()}
            # A transient pricing failure understates equity; persisting that as
            # the reference would show a fake gain forever after. Use it for
            # this response only and try again on the next poll.
            if not pricing_degraded:
                self._save_baseline(environment, baseline)

        baseline_equity = float(baseline.get("equity", 0.0))
        change = equity - baseline_equity
        change_pct = (change / baseline_equity * 100.0) if baseline_equity > 0 else None

        usdt = details.get("USDT", {})
        return AccountSnapshot(
            exchange=status.exchange,
            environment=environment,
            testnet=status.testnet,
            cash_usdt=round(float(usdt.get("total", 0.0)), 8),
            free_usdt=round(float(usdt.get("free", 0.0)), 8),
            locked_usdt=round(float(usdt.get("used", 0.0)), 8),
            equity_usdt=round(equity, 8),
            balances=balances,
            priced_assets=priced,
            unpriced_assets=unpriced,
            realized_pnl=round(realized, 8),
            unrealized_pnl=round(unrealized, 8),
            total_pnl=round(realized + unrealized, 8),
            baseline_equity=round(baseline_equity, 8),
            baseline_at=_parse_datetime(baseline.get("at")),
            change_usdt=round(change, 8),
            change_pct=round(change_pct, 4) if change_pct is not None else None,
            as_of=now,
        )

    async def reset_baseline(
        self, exchange: Any = None, equity: float | None = None
    ) -> AccountSnapshot:
        """Set the reference to the current equity (or an explicit value)."""
        status = self._live.status(exchange)
        environment = "testnet" if status.testnet else "mainnet"
        if equity is None:
            current = await self.snapshot(exchange)
            equity = current.equity_usdt
        now = datetime.now(tz=UTC)
        self._save_baseline(environment, {"equity": float(equity), "at": now.isoformat()})
        logger.info(
            "account_baseline_reset",
            environment=environment,
            equity=round(float(equity), 8),
        )
        return await self.snapshot(exchange)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _price_assets(self, assets: list[str]) -> tuple[dict[str, float], bool]:
        """Price assets in USDT; returns the prices and whether pricing degraded.

        "No USDT market" is a stable answer and is cached for a while so the UI
        poll does not keep paying the fetcher's retry backoff. A failed request
        is transient: it is not cached and marks the result as degraded.
        """
        now = time.monotonic()
        pending = [
            asset for asset in assets if self._unpriced_until.get(f"{asset}/USDT", 0.0) <= now
        ]
        if not pending:
            return {}, False

        symbols = {asset: f"{asset}/USDT" for asset in pending}
        found: dict[str, float] = {}
        degraded = False
        if self._batch_price_lookup is not None:
            try:
                batch = await self._batch_price_lookup(list(symbols.values()))
            except Exception as exc:
                logger.warning("account_batch_price_lookup_failed", error=str(exc))
                return {}, True
            found = {
                asset: float(batch[symbol])
                for asset, symbol in symbols.items()
                if symbol in batch and float(batch[symbol]) > 0
            }
        else:
            results = await asyncio.gather(*(self._single_price(s) for s in symbols.values()))
            for asset, (price, failed) in zip(symbols, results, strict=True):
                degraded = degraded or failed
                if price is not None:
                    found[asset] = price
                elif failed:
                    # Transient: leave it out of the negative cache.
                    symbols[asset] = ""

        for asset, symbol in symbols.items():
            if not symbol:
                continue
            if asset in found:
                self._unpriced_until.pop(symbol, None)
            else:
                self._unpriced_until[symbol] = now + _UNPRICED_RETRY_SECONDS
        return found, degraded

    async def _single_price(self, symbol: str) -> tuple[float | None, bool]:
        """Return (price, request_failed) for one symbol."""
        async with self._price_semaphore:
            try:
                price = await self._price_lookup(symbol)
            except Exception as exc:
                logger.warning("account_price_lookup_failed", symbol=symbol, error=str(exc))
                return None, True
        if price is None or price <= 0:
            return None, False
        return price, False

    @staticmethod
    def _pytest_guard() -> bool:
        return "pytest" in sys.modules and not os.getenv("ACCOUNT_BASELINE_PATH", "").strip()

    def _load_baseline(self, environment: str) -> dict[str, Any] | None:
        if self._pytest_guard():
            return self._memory.get(environment)
        try:
            if not self._baseline_file.exists():
                return None
            raw = json.loads(self._baseline_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("account_baseline_read_failed", error=str(exc))
            return None
        if not isinstance(raw, dict):
            return None
        value = raw.get(environment)
        return value if isinstance(value, dict) else None

    def _save_baseline(self, environment: str, value: dict[str, Any]) -> None:
        if self._pytest_guard():
            self._memory[environment] = value
            return
        try:
            existing: dict[str, Any] = {}
            if self._baseline_file.exists():
                raw = json.loads(self._baseline_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = raw
            existing[environment] = value
            self._baseline_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self._baseline_file.with_suffix(f"{self._baseline_file.suffix}.tmp")
            temp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            os.replace(temp, self._baseline_file)
        except Exception as exc:
            logger.warning("account_baseline_write_failed", error=str(exc))


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
