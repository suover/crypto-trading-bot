from typing import Any

from scripts.check_upbit_order_chance import run_diagnostic


def chance_payload() -> dict[str, object]:
    return {
        "bid_fee": "0.0005",
        "ask_fee": "0.0005",
        "market": {
            "id": "KRW-BTC",
            "order_sides": ["ask", "bid"],
            "bid_types": ["price"],
            "ask_types": ["market"],
            "bid": {"min_total": "5000"},
            "ask": {"min_total": "5000"},
            "max_total": "1000000000",
        },
        "bid_account": {"currency": "KRW", "balance": "100000"},
        "ask_account": {"currency": "BTC", "balance": "1"},
    }


class GetOnlyClient:
    def __init__(self) -> None:
        self.get_calls: list[str] = []

    def get_order_chance(self, market: str) -> dict[str, Any]:
        self.get_calls.append(market)
        return chance_payload()

    def create_market_buy_order(self, **kwargs: object) -> None:
        raise AssertionError("POST /orders must not be called")

    def test_market_buy_order(self, **kwargs: object) -> None:
        raise AssertionError("POST /orders/test must not be called")


def test_order_chance_diagnostic_is_get_only_and_secret_safe(capsys) -> None:
    client = GetOnlyClient()
    assert run_diagnostic(market="KRW-BTC", client=client) is True  # type: ignore[arg-type]
    output = capsys.readouterr().out
    assert client.get_calls == ["KRW-BTC"]
    assert "result=SUCCESS" in output
    assert "market_buy_supported=True" in output
    assert "market_sell_supported=True" in output
    for forbidden in ("Authorization", "Bearer", "JWT", "UPBIT_SECRET_KEY"):
        assert forbidden not in output
