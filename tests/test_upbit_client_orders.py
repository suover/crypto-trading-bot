from decimal import Decimal
from typing import Any

import httpx
import jwt
import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderAmbiguousError,
    UpbitOrderNotFoundError,
    UpbitOrderReadError,
    UpbitOrderRejectedError,
)


class FakeResponse:
    def __init__(self, data: object) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.data


def clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def upbit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", "")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", "")
    monkeypatch.setenv("UPBIT_ACCESS_KEY_FILE", "")
    monkeypatch.setenv("UPBIT_SECRET_KEY_FILE", "")

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://test:test@localhost:5432/test",
    )
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv(
        "UPBIT_SECRET_KEY",
        "test-secret-key-for-jwt-hs512-unit-test-only-0123456789abcdef0123456789abcdef",
    )

    clear_settings_cache()

    yield

    clear_settings_cache()


def test_create_market_buy_order_posts_expected_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, Any] = {}

    def fake_post(
        url: str,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        captured_request["url"] = url
        captured_request["json"] = json
        captured_request["headers"] = headers
        captured_request["timeout"] = timeout

        return FakeResponse(
            {
                "uuid": "order-uuid",
                "market": json["market"],
                "side": json["side"],
                "ord_type": json["ord_type"],
                "price": json["price"],
            }
        )

    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.post",
        fake_post,
    )

    result = UpbitClient().create_market_buy_order(
        market="KRW-BTC",
        amount_krw=Decimal("5000.00"),
        identifier="test-buy-1",
    )

    assert captured_request["url"] == "https://api.upbit.com/v1/orders"
    assert captured_request["json"] == {
        "market": "KRW-BTC",
        "side": "bid",
        "price": "5000",
        "ord_type": "price",
        "identifier": "test-buy-1",
    }
    assert captured_request["headers"]["Authorization"].startswith("Bearer ")
    assert captured_request["headers"]["Content-Type"] == "application/json"
    assert captured_request["timeout"] == 5.0

    assert result["uuid"] == "order-uuid"
    assert result["side"] == "bid"
    assert result["ord_type"] == "price"


def test_create_market_sell_order_posts_expected_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, Any] = {}

    def fake_post(
        url: str,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        captured_request["url"] = url
        captured_request["json"] = json
        captured_request["headers"] = headers
        captured_request["timeout"] = timeout

        return FakeResponse(
            {
                "uuid": "order-uuid",
                "market": json["market"],
                "side": json["side"],
                "ord_type": json["ord_type"],
                "volume": json["volume"],
            }
        )

    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.post",
        fake_post,
    )

    result = UpbitClient().create_market_sell_order(
        market="KRW-BTC",
        quantity=Decimal("0.0001000000"),
        identifier="test-sell-1",
    )

    assert captured_request["url"] == "https://api.upbit.com/v1/orders"
    assert captured_request["json"] == {
        "market": "KRW-BTC",
        "side": "ask",
        "volume": "0.0001",
        "ord_type": "market",
        "identifier": "test-sell-1",
    }
    assert captured_request["headers"]["Authorization"].startswith("Bearer ")
    assert captured_request["headers"]["Content-Type"] == "application/json"
    assert captured_request["timeout"] == 5.0

    assert result["uuid"] == "order-uuid"
    assert result["side"] == "ask"
    assert result["ord_type"] == "market"


def test_create_market_buy_order_rejects_non_positive_amount() -> None:
    with pytest.raises(
        ValueError,
        match="amount_krw must be greater than 0",
    ):
        UpbitClient().create_market_buy_order(
            market="KRW-BTC",
            amount_krw=Decimal("0"),
        )


def test_create_market_sell_order_rejects_non_positive_quantity() -> None:
    with pytest.raises(
        ValueError,
        match="quantity must be greater than 0",
    ):
        UpbitClient().create_market_sell_order(
            market="KRW-BTC",
            quantity=Decimal("0"),
        )


def test_create_authorization_header_includes_query_hash_for_order_body() -> None:
    header = UpbitClient()._create_authorization_header(
        {
            "market": "KRW-BTC",
            "side": "bid",
            "price": "5000",
            "ord_type": "price",
        }
    )

    assert header.startswith("Bearer ")


@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_body"),
    [
        (
            "test_market_buy_order",
            {"market": "KRW-BTC", "amount_krw": Decimal("5000")},
            {
                "market": "KRW-BTC",
                "side": "bid",
                "price": "5000",
                "ord_type": "price",
            },
        ),
        (
            "test_market_sell_order",
            {"market": "KRW-BTC", "quantity": Decimal("0.0001")},
            {
                "market": "KRW-BTC",
                "side": "ask",
                "volume": "0.0001",
                "ord_type": "market",
            },
        ),
    ],
)
def test_order_test_methods_use_only_test_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    kwargs: dict[str, object],
    expected_body: dict[str, str],
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **request: object) -> FakeResponse:
        captured.update({"url": url, **request})
        return FakeResponse({"result": "success"})

    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.post", fake_post
    )
    getattr(UpbitClient(), method_name)(**kwargs)
    assert captured["url"] == "https://api.upbit.com/v1/orders/test"
    assert captured["json"] == expected_body
    assert captured["url"] != "https://api.upbit.com/v1/orders"


@pytest.mark.parametrize(
    ("lookup", "expected_params"),
    [
        ({"uuid": "order-uuid"}, {"uuid": "order-uuid"}),
        ({"identifier": "recommendation-1"}, {"identifier": "recommendation-1"}),
    ],
)
def test_get_order_uses_authenticated_lookup_parameter(
    monkeypatch: pytest.MonkeyPatch,
    lookup: dict[str, str],
    expected_params: dict[str, str],
) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **request: object) -> FakeResponse:
        captured.update({"url": url, **request})
        return FakeResponse({"uuid": "order-uuid"})

    monkeypatch.setattr("crypto_trading_bot.exchange.upbit_client.httpx.get", fake_get)
    UpbitClient().get_order(**lookup)
    assert captured["url"] == "https://api.upbit.com/v1/order"
    assert captured["params"] == expected_params
    token = str(captured["headers"]["Authorization"]).removeprefix("Bearer ")
    payload = jwt.decode(
        token,
        "test-secret-key-for-jwt-hs512-unit-test-only-0123456789abcdef0123456789abcdef",
        algorithms=["HS512"],
    )
    assert "query_hash" in payload


@pytest.mark.parametrize(
    "lookup",
    [{}, {"uuid": "x", "identifier": "y"}, {"uuid": " "}, {"identifier": ""}],
)
def test_get_order_rejects_invalid_lookup_values(lookup: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        UpbitClient().get_order(**lookup)


def test_get_order_chance_uses_authenticated_market_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **request: object) -> FakeResponse:
        captured.update({"url": url, **request})
        return FakeResponse({"market": {"id": "KRW-BTC"}})

    monkeypatch.setattr("crypto_trading_bot.exchange.upbit_client.httpx.get", fake_get)
    result = UpbitClient().get_order_chance(" KRW-BTC ")
    assert result == {"market": {"id": "KRW-BTC"}}
    assert captured["url"] == "https://api.upbit.com/v1/orders/chance"
    assert captured["params"] == {"market": "KRW-BTC"}
    token = str(captured["headers"]["Authorization"]).removeprefix("Bearer ")
    payload = jwt.decode(
        token,
        "test-secret-key-for-jwt-hs512-unit-test-only-0123456789abcdef0123456789abcdef",
        algorithms=["HS512"],
    )
    assert "query_hash" in payload


def test_get_order_chance_rejects_blank_market() -> None:
    with pytest.raises(ValueError, match="market must not be empty"):
        UpbitClient().get_order_chance(" ")


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_get_order_chance_http_failure_is_read_only_error(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    request = httpx.Request("GET", "https://api.upbit.com/v1/orders/chance")
    response = httpx.Response(status, request=request, json={"error": {}})
    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.get",
        lambda *args, **kwargs: response,
    )
    with pytest.raises(UpbitOrderReadError) as caught:
        UpbitClient().get_order_chance("KRW-BTC")
    assert caught.value.safe_error.operation == "get_order_chance"
    assert caught.value.safe_error.status_code == status


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("timeout"), httpx.ConnectError("connection")]
)
def test_get_order_chance_transport_failure_is_not_ambiguous(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fake_get(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("crypto_trading_bot.exchange.upbit_client.httpx.get", fake_get)
    with pytest.raises(UpbitOrderReadError):
        UpbitClient().get_order_chance("KRW-BTC")


@pytest.mark.parametrize("content", [b"not-json", b"[]"])
def test_get_order_chance_rejects_invalid_or_non_object_response(
    monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    request = httpx.Request("GET", "https://api.upbit.com/v1/orders/chance")
    response = httpx.Response(200, request=request, content=content)
    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.get",
        lambda *args, **kwargs: response,
    )
    with pytest.raises(UpbitOrderReadError):
        UpbitClient().get_order_chance("KRW-BTC")


def test_get_order_404_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.upbit.com/v1/order")
    response = httpx.Response(
        404,
        request=request,
        json={"error": {"name": "order_not_found", "message": "not found"}},
    )

    def fake_get(*args: object, **kwargs: object) -> httpx.Response:
        return response

    monkeypatch.setattr("crypto_trading_bot.exchange.upbit_client.httpx.get", fake_get)
    with pytest.raises(UpbitOrderNotFoundError):
        UpbitClient().get_order(identifier="recommendation-1")


def test_create_4xx_is_rejected_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://api.upbit.com/v1/orders")
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "name": "validation_error",
                "message": (
                    "test-access-key "
                    "test-secret-key-for-jwt-hs512-unit-test-only-"
                    "0123456789abcdef0123456789abcdef"
                ),
            }
        },
    )

    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.post",
        lambda *args, **kwargs: response,
    )
    with pytest.raises(UpbitOrderRejectedError) as captured:
        UpbitClient().create_market_buy_order("KRW-BTC", Decimal("5000"))
    assert "test-access-key" not in str(captured.value)
    assert "test-secret-key-for-jwt" not in str(captured.value)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timeout"),
        httpx.ConnectError("connection failed"),
    ],
)
def test_create_transport_failure_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def fake_post(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.post", fake_post
    )
    with pytest.raises(UpbitOrderAmbiguousError):
        UpbitClient().create_market_buy_order("KRW-BTC", Decimal("5000"))


def test_create_5xx_with_malformed_body_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://api.upbit.com/v1/orders")
    response = httpx.Response(503, request=request, content=b"not-json")
    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.post",
        lambda *args, **kwargs: response,
    )
    with pytest.raises(UpbitOrderAmbiguousError) as captured:
        UpbitClient().create_market_buy_order("KRW-BTC", Decimal("5000"))
    assert "Authorization" not in str(captured.value)
