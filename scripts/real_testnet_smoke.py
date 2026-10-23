#!/usr/bin/env python3
"""Manual smoke test for the real-trading API (testnet by default).

The script is intentionally conservative:

* It refuses to trade unless ``--execute`` and ``--yes`` are provided.
* It refuses mainnet unless ``--allow-mainnet`` is also provided.
* Without ``--execute`` it only checks readiness and sends a test alert.

Usage (backend running on http://localhost:8000):

    python scripts/real_testnet_smoke.py
    python scripts/real_testnet_smoke.py --execute --yes --quantity 0.0001
    python scripts/real_testnet_smoke.py --execute --yes --close

The order is always guarded by the backend's safety interlocks. When
``REAL_TRADING_OCO_ENABLED=true``, the script also reports whether the
exchange-side OCO was attached to the new position. This script is not a
substitute for reading the status/positions and confirming the exchange
environment in the UI.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("ATA_API_URL", "http://localhost:8000")


def _print_json(label: str, payload: Any) -> None:
    print(f"\n=== {label} ===")
    print(payload)


def _fail(message: str, response: httpx.Response | None = None) -> None:
    print(f"\nERROR: {message}")
    if response is not None:
        try:
            print(response.json())
        except Exception:
            print(response.text)
    raise SystemExit(1)


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        _fail(f"{method} {path} returned HTTP {response.status_code}", response)
    return response.json()


def _last_price(client: httpx.Client, symbol: str) -> float:
    encoded = symbol.replace("/", "%2F")
    payload = _request(
        client,
        "GET",
        f"/api/v1/market/{encoded}/candles",
        params={"timeframe": "1m", "limit": 2},
    )
    candles = payload.get("data") or []
    if not candles:
        _fail(f"No candles returned for {symbol}; cannot derive a reference price.")
    return float(candles[-1]["close"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-trading API smoke test (testnet-first)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Backend base URL")
    parser.add_argument("--symbol", default="BTC/USDT", help="Spot symbol")
    parser.add_argument("--quantity", type=float, default=0.0001, help="Order quantity")
    parser.add_argument("--stop-pct", type=float, default=2.0, help="Stop-loss distance in %%")
    parser.add_argument("--take-pct", type=float, default=4.0, help="Take-profit distance in %%")
    parser.add_argument(
        "--execute", action="store_true", help="Actually place a real testnet order"
    )
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")
    parser.add_argument("--close", action="store_true", help="Close the position after the order")
    parser.add_argument("--allow-mainnet", action="store_true", help="Allow a mainnet smoke test")
    parser.add_argument("--skip-alert", action="store_true", help="Do not send a test alert")
    args = parser.parse_args()

    symbol = args.symbol.replace("-", "/").replace("_", "/").upper()

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        status = _request(client, "GET", "/api/v1/trading/real/status")
        _print_json("REAL TRADING STATUS", status)

        if not status.get("enabled"):
            _fail(
                "Real trading is disabled. Set EXECUTION_MODE=real and "
                "ENABLE_REAL_TRADING=true, then restart the backend."
            )
        if not status.get("credentials_configured"):
            _fail("No vault credentials configured for the selected exchange.")

        is_testnet = bool(status.get("testnet"))
        if not is_testnet and not args.allow_mainnet:
            _fail(
                "Mainnet credentials detected. Refusing to continue without "
                "--allow-mainnet. Use testnet first."
            )

        if symbol not in [item.upper() for item in status.get("allowed_symbols", [])]:
            _fail(f"{symbol} is not in REAL_TRADING_ALLOWED_SYMBOLS.")

        if not args.skip_alert:
            alert = _request(
                client,
                "POST",
                "/api/v1/alerts/test",
                json={"severity": "info"},
            )
            _print_json("TEST ALERT", alert)
            if not alert.get("delivered"):
                print(
                    "WARNING: the test alert was not delivered; check ALERTS_ENABLED and channels."
                )

        if not args.execute:
            positions = _request(client, "GET", "/api/v1/trading/real/positions")
            _print_json("OPEN REAL POSITIONS", positions)
            print("\nDry run complete. Add --execute --yes to place a testnet order.")
            return 0

        if not is_testnet and not args.allow_mainnet:
            _fail("Mainnet order blocked by safety policy.")
        if not args.yes:
            venue = "TESTNET" if is_testnet else "MAINNET (REAL FUNDS)"
            answer = input(f"Place BUY {args.quantity} {symbol} on {venue}? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Cancelled.")
                return 0

        reference = _last_price(client, symbol)
        stop_loss = round(reference * (1 - args.stop_pct / 100.0), 8)
        take_profit = round(reference * (1 + args.take_pct / 100.0), 8)
        client_order_id = f"smoke-{int(time.time())}"

        order = _request(
            client,
            "POST",
            "/api/v1/trading/real/orders",
            json={
                "symbol": symbol,
                "side": "buy",
                "order_type": "market",
                "quantity": args.quantity,
                "price": reference,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "exchange": status.get("exchange", "binance"),
                "confirm": True,
                "client_order_id": client_order_id,
                "notes": "real_testnet_smoke.py",
            },
        )
        _print_json("REAL ORDER", order)

        positions = _request(client, "GET", "/api/v1/trading/real/positions")
        _print_json("OPEN REAL POSITIONS", positions)

        if status.get("oco_enabled"):
            oco_status = _request(client, "GET", "/api/v1/trading/real/status")
            _print_json(
                "OCO STATUS",
                {
                    "oco_enabled": oco_status.get("oco_enabled"),
                    "oco_protected_positions": oco_status.get("oco_protected_positions"),
                    "protection_mode": oco_status.get("protection_mode"),
                    "oco_discovery_failed_exchanges": oco_status.get(
                        "oco_discovery_failed_exchanges"
                    ),
                },
            )
            if not oco_status.get("oco_protected_positions"):
                print(
                    "WARNING: OCO is enabled but no protected position was reported. "
                    "Check the alerts and backend logs before leaving the position open."
                )

        if args.close:
            if not args.yes:
                answer = input("Close the newly opened position now? [y/N] ").strip().lower()
                if answer not in {"y", "yes"}:
                    print("Position left open; close it manually from the API/UI.")
                    return 0
            close = _request(
                client,
                "POST",
                "/api/v1/trading/real/positions/close",
                json={
                    "symbol": symbol,
                    "quantity": args.quantity,
                    "current_price": reference,
                    "exchange": status.get("exchange", "binance"),
                    "confirm": True,
                },
            )
            _print_json("CLOSE ORDER", close)

        print("\nSmoke test complete. Verify the order and position in the exchange UI.")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
