"""Unit tests for notification channels and the low-level live exchange adapter."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

import ccxt.async_support as ccxt
import pytest

from src.domain.market.entities import Exchange
from src.domain.notifications.entities import Alert, AlertSeverity
from src.domain.trading.entities import OrderSide, OrderStatus, OrderType
from src.infrastructure.exchange.live_exchange import LiveExchangeAdapter
from src.infrastructure.notifications.email import EmailChannel
from src.infrastructure.notifications.telegram import TelegramChannel
from src.infrastructure.notifications.webhook import WebhookChannel
from src.shared.exceptions.domain import ExchangeError, SecurityError


def make_alert() -> Alert:
    return Alert(title="Título", message="Mensaje", severity=AlertSeverity.WARNING)


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeHttpClient:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response = response or FakeResponse()
        self.error = error
        self.posted_url: str | None = None
        self.posted_json: dict[str, Any] | None = None

    async def __aenter__(self) -> FakeHttpClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        self.posted_url = url
        self.posted_json = json
        if self.error is not None:
            raise self.error
        return self.response


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def test_telegram_requires_token_and_chat() -> None:
    assert TelegramChannel("", "").is_configured is False
    assert TelegramChannel("token", "").is_configured is False
    assert TelegramChannel("token", "chat").is_configured is True


async def test_telegram_skips_unconfigured_channel() -> None:
    assert await TelegramChannel("", "").send(make_alert()) is False


async def test_telegram_sends_message(monkeypatch: Any) -> None:
    client = FakeHttpClient()
    monkeypatch.setattr(
        "src.infrastructure.notifications.telegram.httpx.AsyncClient",
        lambda **_: client,
    )

    delivered = await TelegramChannel("token", "chat").send(make_alert())

    assert delivered is True
    assert client.posted_url is not None and "token" in client.posted_url
    assert client.posted_json is not None
    assert client.posted_json["chat_id"] == "chat"
    assert client.posted_json["text"].startswith("[WARNING] Título")


async def test_telegram_reports_http_error(monkeypatch: Any) -> None:
    client = FakeHttpClient(FakeResponse(status_code=400, text="bad request"))
    monkeypatch.setattr(
        "src.infrastructure.notifications.telegram.httpx.AsyncClient",
        lambda **_: client,
    )

    assert await TelegramChannel("token", "chat").send(make_alert()) is False


async def test_telegram_survives_transport_error(monkeypatch: Any) -> None:
    client = FakeHttpClient(error=RuntimeError("network"))
    monkeypatch.setattr(
        "src.infrastructure.notifications.telegram.httpx.AsyncClient",
        lambda **_: client,
    )

    assert await TelegramChannel("token", "chat").send(make_alert()) is False


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


async def test_webhook_skips_unconfigured() -> None:
    assert await WebhookChannel("").send(make_alert()) is False


async def test_webhook_posts_alert_json(monkeypatch: Any) -> None:
    client = FakeHttpClient()
    monkeypatch.setattr(
        "src.infrastructure.notifications.webhook.httpx.AsyncClient",
        lambda **_: client,
    )

    delivered = await WebhookChannel("https://example.test/hook").send(make_alert())

    assert delivered is True
    assert client.posted_json is not None
    assert client.posted_json["title"] == "Título"
    assert client.posted_json["severity"] == "warning"


async def test_webhook_reports_failure(monkeypatch: Any) -> None:
    client = FakeHttpClient(error=RuntimeError("network"))
    monkeypatch.setattr(
        "src.infrastructure.notifications.webhook.httpx.AsyncClient",
        lambda **_: client,
    )

    assert await WebhookChannel("https://example.test/hook").send(make_alert()) is False


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class FakeSmtp:
    instances: ClassVar[list[FakeSmtp]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = False
        self.message: Any = None
        FakeSmtp.instances.append(self)

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = True

    def send_message(self, message: Any) -> None:
        if FakeSmtp.error is not None:
            raise FakeSmtp.error
        self.message = message


def test_email_requires_configuration() -> None:
    assert EmailChannel("", 587, "", "", "", "").is_configured is False
    assert EmailChannel("smtp.test", 587, "", "", "a@test", "").is_configured is False
    assert EmailChannel("smtp.test", 587, "", "", "a@test", "b@test").is_configured is True


async def test_email_skips_unconfigured() -> None:
    channel = EmailChannel("", 587, "", "", "", "")
    assert await channel.send(make_alert()) is False


async def test_email_sends_via_smtp(monkeypatch: Any) -> None:
    FakeSmtp.instances = []
    FakeSmtp.error = None
    monkeypatch.setattr(
        "src.infrastructure.notifications.email.smtplib.SMTP",
        FakeSmtp,
    )
    channel = EmailChannel(
        "smtp.test",
        587,
        "user",
        "pass",
        "bot@test",
        "ops@test, backup@test",
    )

    delivered = await channel.send(make_alert())

    assert delivered is True
    server = FakeSmtp.instances[0]
    assert server.started_tls is True
    assert server.logged_in is True
    assert server.message["To"] == "ops@test, backup@test"


async def test_email_survives_smtp_error(monkeypatch: Any) -> None:
    FakeSmtp.error = RuntimeError("smtp down")
    monkeypatch.setattr(
        "src.infrastructure.notifications.email.smtplib.SMTP",
        FakeSmtp,
    )
    channel = EmailChannel("smtp.test", 587, "", "", "bot@test", "ops@test")

    assert await channel.send(make_alert()) is False
    FakeSmtp.error = None


# ---------------------------------------------------------------------------
# LiveExchangeAdapter
# ---------------------------------------------------------------------------


class FakeCcxtClient:
    def __init__(self) -> None:
        self.closed = False
        self.error: Exception | None = None
        self.response: dict[str, Any] = {
            "id": "exchange-order-1",
            "status": "closed",
            "filled": "0.1",
            "average": "50000",
            "fee": {"cost": "1.5", "currency": "USDT"},
        }
        self.create_kwargs: dict[str, Any] = {}
        self.load_markets_calls = 0
        self.markets: dict[str, Any] = {"BTC/USDT": {"id": "BTCUSDT", "symbol": "BTC/USDT"}}
        self.markets_by_id: dict[str, Any] = {"BTCUSDT": [{"id": "BTCUSDT", "symbol": "BTC/USDT"}]}
        self.oco_create_params: dict[str, Any] = {}
        self.oco_cancel_params: dict[str, Any] = {}
        self.oco_fetch_params: dict[str, Any] = {}
        self.oco_leg_params: list[dict[str, Any]] = []
        self.oco_leg_details: dict[int, dict[str, Any]] = {}
        self.oco_leg_error: Exception | None = None
        self.open_oco_params: dict[str, Any] = {}
        self.oco_create_response: dict[str, Any] = {
            "orderListId": 123,
            "listOrderStatus": "EXECUTING",
            "orders": [],
        }
        self.oco_status_response: dict[str, Any] = {
            "orderListId": 123,
            "listOrderStatus": "EXECUTING",
            "orders": [],
        }
        self.open_oco_response: list[dict[str, Any]] = []

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{amount:.2f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{price:.2f}"

    async def fetch_time(self) -> int:
        return 1_700_000_000_000

    async def fetch_balance(self) -> dict[str, Any]:
        return {
            "total": {"USDT": 100.0, "BTC": 0.5, "DUST": 0.0},
            "free": {"BTC": 0.4, "USDT": 100.0},
        }

    async def create_order(self, **kwargs: Any) -> dict[str, Any]:
        self.create_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response

    def market(self, symbol: str) -> dict[str, Any]:
        return self.markets[symbol]

    async def load_markets(self) -> dict[str, Any]:
        self.load_markets_calls += 1
        if not self.markets:
            self.markets = {"BTC/USDT": {"id": "BTCUSDT", "symbol": "BTC/USDT"}}
        return self.markets

    async def private_post_order_oco(self, params: dict[str, Any]) -> dict[str, Any]:
        self.oco_create_params = params
        return self.oco_create_response

    async def private_delete_orderlist(self, params: dict[str, Any]) -> dict[str, Any]:
        self.oco_cancel_params = params
        return {"orderListId": params["orderListId"], "listOrderStatus": "ALL_DONE"}

    async def private_get_orderlist(self, params: dict[str, Any]) -> dict[str, Any]:
        self.oco_fetch_params = params
        return self.oco_status_response

    async def private_get_order(self, params: dict[str, Any]) -> dict[str, Any]:
        self.oco_leg_params.append(params)
        if self.oco_leg_error is not None:
            raise self.oco_leg_error
        order_id = int(params["orderId"])
        return self.oco_leg_details.get(
            order_id,
            {
                "orderId": order_id,
                "status": "CANCELED",
                "type": "LIMIT_MAKER",
                "executedQty": "0",
                "cummulativeQuoteQty": "0",
            },
        )

    async def private_get_openorderlist(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.open_oco_params = params
        return self.open_oco_response

    async def close(self) -> None:
        self.closed = True


def make_adapter(api_key: str = "key", api_secret: str = "secret") -> LiveExchangeAdapter:
    adapter = LiveExchangeAdapter(
        exchange=Exchange.BINANCE,
        api_key=api_key,
        api_secret=api_secret,
        is_testnet=False,
    )
    adapter._client = FakeCcxtClient()
    return adapter


def make_bybit_adapter() -> LiveExchangeAdapter:
    adapter = LiveExchangeAdapter(
        exchange=Exchange.BYBIT,
        api_key="key",
        api_secret="secret",
        is_testnet=False,
    )
    adapter._client = FakeCcxtClient()
    return adapter


async def test_live_adapter_ping() -> None:
    adapter = make_adapter()
    result = await adapter.ping()

    assert result["status"] == "connected"
    assert result["exchange"] == "binance"
    assert result["is_testnet"] is False


async def test_live_adapter_balances_exclude_dust() -> None:
    adapter = make_adapter()
    balances = await adapter.fetch_balances()

    assert balances == {"USDT": 100.0, "BTC": 0.5}


async def test_live_adapter_free_balance() -> None:
    adapter = make_adapter()
    assert await adapter.fetch_free_balance("BTC") == 0.4


async def test_live_adapter_requires_credentials() -> None:
    adapter = make_adapter(api_key="", api_secret="")

    with pytest.raises(SecurityError):
        await adapter.fetch_balances()

    with pytest.raises(SecurityError):
        await adapter.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1)


async def test_live_adapter_market_order_mapping() -> None:
    adapter = make_adapter()

    order = await adapter.place_order(
        "BTC/USDT",
        OrderSide.BUY,
        OrderType.MARKET,
        0.1,
        client_order_id="client-1",
    )

    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("0.1")
    assert order.average_fill_price == Decimal("50000")
    assert order.commission == Decimal("1.5")
    assert order.client_order_id == "client-1"
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    assert client.create_kwargs["amount"] == 0.10
    assert client.create_kwargs["params"]["newClientOrderId"] == "client-1"


async def test_live_adapter_uses_bybit_order_link_id() -> None:
    adapter = make_bybit_adapter()

    order = await adapter.place_order(
        "BTC/USDT",
        OrderSide.BUY,
        OrderType.MARKET,
        0.1,
        client_order_id="client-bybit",
    )

    assert order.client_order_id == "client-bybit"
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    assert client.create_kwargs["params"]["orderLinkId"] == "client-bybit"


async def test_live_adapter_limit_order_mapping() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.response = {"id": "2", "status": "open", "filled": "0", "fee": None}

    order = await adapter.place_order(
        "BTC/USDT",
        OrderSide.BUY,
        OrderType.LIMIT,
        0.1,
        price=49_999,
    )

    assert order.status == OrderStatus.OPEN
    assert order.limit_price == Decimal("49999")
    assert client.create_kwargs["type"] == "limit"
    assert client.create_kwargs["price"] == 49_999


async def test_live_adapter_stop_order_sets_stop_price() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)

    await adapter.place_order(
        "BTC/USDT",
        OrderSide.SELL,
        OrderType.STOP_MARKET,
        0.1,
        stop_price=48_000,
    )

    assert client.create_kwargs["type"] == "market"
    assert client.create_kwargs["params"]["stopPrice"] == 48_000


async def test_live_adapter_requires_limit_price() -> None:
    adapter = make_adapter()

    with pytest.raises(ExchangeError, match="limit price"):
        await adapter.place_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.1)


async def test_live_adapter_maps_auth_error() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.error = ccxt.AuthenticationError("bad key")

    with pytest.raises(SecurityError, match="authentication"):
        await adapter.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1)


async def test_live_adapter_maps_insufficient_funds() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.error = ccxt.InsufficientFunds("no funds")

    with pytest.raises(ExchangeError, match="Insufficient funds"):
        await adapter.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1)


async def test_live_adapter_maps_network_error() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.error = ccxt.NetworkError("timeout")

    with pytest.raises(ExchangeError, match="Network error"):
        await adapter.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1)


async def test_live_adapter_maps_generic_error() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.error = RuntimeError("boom")

    with pytest.raises(ExchangeError, match="Live order failed"):
        await adapter.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1)


async def test_live_adapter_closes_client() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)

    await adapter.close()

    assert client.closed is True
    assert adapter._client is None


async def test_live_adapter_create_oco_mapping() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)

    response = await adapter.create_oco_sell(
        "BTC/USDT",
        quantity=0.1,
        take_profit=52_000,
        stop_loss=48_000,
        stop_limit_price=47_900,
        list_client_order_id="oco-list-1",
    )

    assert response["orderListId"] == 123
    assert client.oco_create_params == {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "quantity": "0.10",
        "price": "52000.00",
        "stopPrice": "48000.00",
        "stopLimitPrice": "47900.00",
        "stopLimitTimeInForce": "GTC",
        "newOrderRespType": "FULL",
        "listClientOrderId": "oco-list-1",
    }


async def test_live_adapter_oco_rejects_non_binance() -> None:
    adapter = make_bybit_adapter()

    with pytest.raises(ExchangeError, match="only implemented for Binance"):
        await adapter.create_oco_sell("BTC/USDT", 0.1, 52_000, 48_000)


async def test_live_adapter_oco_rejects_invalid_prices() -> None:
    adapter = make_adapter()

    with pytest.raises(ExchangeError, match="take-profit must be above"):
        await adapter.create_oco_sell("BTC/USDT", 0.1, 48_000, 49_000)


async def test_live_adapter_oco_cancel_fetch_and_open_lists() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.open_oco_response = [
        {"orderListId": 123, "symbol": "BTCUSDT", "listOrderStatus": "EXECUTING"}
    ]

    await adapter.cancel_oco("BTC/USDT", 123)
    await adapter.fetch_oco("BTC/USDT", 123)
    lists = await adapter.fetch_open_oco_lists()
    by_symbol = await adapter.fetch_open_oco_lists("BTC/USDT")
    normalized = await adapter.normalize_symbol("BTCUSDT")

    assert client.oco_cancel_params == {"symbol": "BTCUSDT", "orderListId": 123}
    assert client.oco_fetch_params == {"orderListId": 123}
    assert lists == client.open_oco_response
    assert by_symbol == client.open_oco_response
    assert client.open_oco_params == {"symbol": "BTCUSDT"}
    assert normalized == "BTC/USDT"


async def test_live_adapter_enriches_terminal_oco_legs() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.oco_status_response = {
        "orderListId": 123,
        "listOrderStatus": "ALL_DONE",
        "orders": [
            {"symbol": "BTCUSDT", "orderId": 11, "clientOrderId": "stop-leg"},
            {"symbol": "BTCUSDT", "orderId": 12, "clientOrderId": "tp-leg"},
        ],
    }
    client.oco_leg_details = {
        11: {
            "orderId": 11,
            "status": "FILLED",
            "type": "STOP_LOSS_LIMIT",
            "executedQty": "0.1",
            "cummulativeQuoteQty": "4900",
            "price": "48000",
            "stopPrice": "49000",
        },
        12: {
            "orderId": 12,
            "status": "EXPIRED",
            "type": "LIMIT_MAKER",
            "executedQty": "0",
            "cummulativeQuoteQty": "0",
            "price": "55000",
            "stopPrice": "0",
        },
    }

    data = await adapter.fetch_oco("BTC/USDT", 123)

    assert data["orders"][0]["status"] == "FILLED"
    assert data["orders"][0]["executedQty"] == "0.1"
    assert data["orders"][1]["status"] == "EXPIRED"
    assert client.oco_leg_params == [
        {"symbol": "BTCUSDT", "orderId": 11},
        {"symbol": "BTCUSDT", "orderId": 12},
    ]


async def test_live_adapter_terminal_oco_leg_error_is_not_treated_as_empty() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.oco_status_response = {
        "orderListId": 123,
        "listOrderStatus": "ALL_DONE",
        "orders": [{"symbol": "BTCUSDT", "orderId": 11, "clientOrderId": "stop-leg"}],
    }
    client.oco_leg_error = RuntimeError("leg unavailable")

    with pytest.raises(ExchangeError, match="Failed to fetch OCO leg"):
        await adapter.fetch_oco("BTC/USDT", 123)


async def test_live_adapter_oco_discovery_loads_markets_first() -> None:
    adapter = make_adapter()
    client = adapter._client
    assert isinstance(client, FakeCcxtClient)
    client.markets = {}

    await adapter.fetch_open_oco_lists()

    assert client.load_markets_calls == 1
