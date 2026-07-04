from decimal import Decimal
from typing import Any

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient


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
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
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
