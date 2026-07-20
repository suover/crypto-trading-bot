from typing import Any

import pytest

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.market_data.coingecko_client import CoinGeckoClient
from crypto_trading_bot.market_data.fear_greed_client import FearGreedClient


class Response:
    def __init__(self, data: object) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self.data


def settings(**overrides: object) -> Settings:
    return Settings(database_url="postgresql://test:test@localhost/test", **overrides)


def test_upbit_orderbook_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def get(url: str, **kwargs: Any) -> Response:
        captured.update(url=url, **kwargs)
        return Response([])

    monkeypatch.setattr("crypto_trading_bot.exchange.upbit_client.httpx.get", get)
    UpbitClient().get_orderbooks(["KRW-BTC", "KRW-ETH"], 10)
    assert captured["url"] == "https://api.upbit.com/v1/orderbook"
    assert captured["params"] == {"markets": "KRW-BTC,KRW-ETH", "count": 10}
    assert "headers" not in captured


def test_upbit_orderbook_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        UpbitClient().get_orderbooks([])
    for count in (0, 31):
        with pytest.raises(ValueError, match="between 1 and 30"):
            UpbitClient().get_orderbooks(["KRW-BTC"], count)
    monkeypatch.setattr(
        "crypto_trading_bot.exchange.upbit_client.httpx.get",
        lambda *args, **kwargs: Response({}),
    )
    with pytest.raises(ValueError, match="response format"):
        UpbitClient().get_orderbooks(["KRW-BTC"])


@pytest.mark.parametrize(
    ("base_url", "api_key", "expected_headers"),
    [
        ("https://api.coingecko.com/api/v3", "", None),
        (
            "https://api.coingecko.com/api/v3",
            "demo-secret",
            {"x-cg-demo-api-key": "demo-secret"},
        ),
        (
            "https://pro-api.coingecko.com/api/v3",
            "pro-secret",
            {"x-cg-pro-api-key": "pro-secret"},
        ),
    ],
)
def test_coingecko_request_and_auth_headers(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    api_key: str,
    expected_headers: dict[str, str] | None,
) -> None:
    captured: dict[str, Any] = {}

    def get(url: str, **kwargs: Any) -> Response:
        captured.update(url=url, **kwargs)
        return Response([])

    monkeypatch.setattr(
        "crypto_trading_bot.market_data.coingecko_client.httpx.get", get
    )
    CoinGeckoClient(
        settings(
            coingecko_api_base_url=base_url,
            coingecko_api_key=api_key,
        )
    ).get_markets(["bitcoin", "ethereum", "bitcoin"])
    assert captured["url"] == f"{base_url}/coins/markets"
    assert captured["params"] == {
        "vs_currency": "usd",
        "ids": "bitcoin,ethereum",
        "price_change_percentage": "1h,24h,7d,30d",
        "sparkline": "false",
    }
    assert captured["headers"] == expected_headers


def test_coingecko_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinGeckoClient(settings())
    with pytest.raises(ValueError, match="must not be empty"):
        client.get_markets([])
    monkeypatch.setattr(
        "crypto_trading_bot.market_data.coingecko_client.httpx.get",
        lambda *args, **kwargs: Response({}),
    )
    with pytest.raises(ValueError, match="response format"):
        client.get_markets(["bitcoin"])


def test_fear_greed_parses_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "crypto_trading_bot.market_data.fear_greed_client.httpx.get",
        lambda *args, **kwargs: Response(
            {
                "data": [
                    {
                        "value": "42",
                        "value_classification": "Fear",
                        "timestamp": "123",
                        "time_until_update": "99",
                    }
                ],
                "metadata": {"error": None},
            }
        ),
    )
    assert FearGreedClient(settings()).get_latest() == {
        "available": True,
        "source": "alternative_me_fear_greed",
        "value": 42,
        "value_classification": "Fear",
        "timestamp": 123,
        "time_until_update": 99,
        "attribution": "alternative.me",
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": [], "metadata": {}}, "non-empty list"),
        ({"data": [{}], "metadata": {"error": "rate limited"}}, "API error"),
    ],
)
def test_fear_greed_rejects_invalid_payloads(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], message: str
) -> None:
    monkeypatch.setattr(
        "crypto_trading_bot.market_data.fear_greed_client.httpx.get",
        lambda *args, **kwargs: Response(payload),
    )
    with pytest.raises(ValueError, match=message):
        FearGreedClient(settings()).get_latest()
