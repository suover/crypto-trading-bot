from decimal import Decimal

from crypto_trading_bot.config.settings import get_settings
from scripts.check_upbit_order_test import parse_args, run_order_test


class FakeClient:
    def __init__(self) -> None:
        self.test_calls: list[dict[str, object]] = []

    def test_market_buy_order(self, **kwargs: object) -> dict[str, object]:
        self.test_calls.append(kwargs)
        return {"result": "success"}

    def create_market_buy_order(self, **kwargs: object) -> dict[str, object]:
        raise AssertionError("Actual order method must never be called")


def test_defaults() -> None:
    args = parse_args([])
    assert args.market == "KRW-BTC"
    assert args.amount_krw == "5000"


def test_core_calls_only_official_order_test(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "fake-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "fake-secret")
    monkeypatch.setenv("ALLOWED_MARKETS", "KRW-BTC")
    monkeypatch.setenv("MAX_ORDER_AMOUNT_KRW", "5000")
    get_settings.cache_clear()
    client = FakeClient()
    run_order_test(
        market="KRW-BTC",
        amount_krw=Decimal("5000"),
        client=client,  # type: ignore[arg-type]
        identifier_factory=lambda: "unique",
    )
    assert client.test_calls == [
        {
            "market": "KRW-BTC",
            "amount_krw": Decimal("5000"),
            "identifier": "order-test-unique",
        }
    ]
    get_settings.cache_clear()
