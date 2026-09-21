from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_trading_bot.services.upbit_order_chance_service import (
    UpbitOrderChancePreflightService,
    UpbitOrderChanceValidationError,
    parse_upbit_order_chance,
)


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def chance_payload() -> dict[str, object]:
    return {
        "bid_fee": "0.0005",
        "ask_fee": "0.0005",
        "market": {
            "id": "KRW-BTC",
            "order_sides": ["ask", "bid"],
            "bid_types": ["limit", "price"],
            "ask_types": ["limit", "market"],
            "order_types": ["deprecated-must-not-be-used"],
            "bid": {"currency": "KRW", "min_total": "5000"},
            "ask": {"currency": "KRW", "min_total": "5000"},
            "max_total": "1000000000",
            "state": "active",
        },
        "bid_account": {
            "currency": "KRW",
            "balance": "100000.0000",
            "locked": "0",
        },
        "ask_account": {
            "currency": "BTC",
            "balance": "0.01000000",
            "locked": "0",
        },
    }


def parse(payload: dict[str, object] | None = None):
    return parse_upbit_order_chance(
        payload or chance_payload(), expected_market="KRW-BTC"
    )


def test_parse_normalizes_current_order_chance_fields_as_decimal() -> None:
    chance = parse()
    assert chance.rules.market == "KRW-BTC"
    assert chance.bid_fee == Decimal("0.0005")
    assert chance.ask_fee == Decimal("0.0005")
    assert chance.rules.bid_min_total == Decimal("5000")
    assert chance.rules.ask_min_total == Decimal("5000")
    assert chance.rules.max_total == Decimal("1000000000")
    assert chance.rules.bid_types == ("limit", "price")
    assert chance.rules.ask_types == ("limit", "market")
    assert chance.bid_account.available_balance == Decimal("100000.0000")
    assert chance.ask_account.available_balance == Decimal("0.01000000")


def set_nested(
    payload: dict[str, object], path: tuple[str, ...], value: object
) -> None:
    target = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "reason_code"),
    [
        (("market", "id"), None, "INVALID_CHANCE_RESPONSE"),
        (("market", "id"), "KRW-ETH", "MARKET_MISMATCH"),
        (("bid_fee",), "NaN", "INVALID_FEE"),
        (("bid_fee",), "Infinity", "INVALID_FEE"),
        (("ask_fee",), "-0.1", "INVALID_FEE"),
        (("bid_account", "balance"), "-1", "INVALID_CHANCE_RESPONSE"),
        (("ask_account", "balance"), {}, "INVALID_CHANCE_RESPONSE"),
        (("market", "bid", "min_total"), "bad", "INVALID_CHANCE_RESPONSE"),
        (("market", "max_total"), None, "INVALID_CHANCE_RESPONSE"),
        (("market", "bid_types"), "price", "INVALID_CHANCE_RESPONSE"),
        (("market", "ask_types"), ["market", 1], "INVALID_CHANCE_RESPONSE"),
    ],
)
def test_parser_rejects_malformed_required_fields(path, value, reason_code) -> None:
    payload = deepcopy(chance_payload())
    set_nested(payload, path, value)
    with pytest.raises(UpbitOrderChanceValidationError) as caught:
        parse(payload)
    assert caught.value.reason_code == reason_code


def test_buy_preflight_checks_fee_reserve_at_exact_decimal_boundary() -> None:
    payload = chance_payload()
    payload["bid_account"]["balance"] = "50025"  # type: ignore[index]
    chance = parse(payload)
    service = UpbitOrderChancePreflightService(now_fn=lambda: NOW)
    result = service.validate_buy(
        chance, market="KRW-BTC", approved_amount_krw=Decimal("50000")
    )
    assert result.audit["result"] == "PASSED"
    assert result.audit["fee_reserve_krw"] == "25.0000"
    assert result.audit["approved_amount_krw"] == "50000"

    payload["bid_account"]["balance"] = "50024.9999"  # type: ignore[index]
    with pytest.raises(UpbitOrderChanceValidationError) as caught:
        service.validate_buy(
            parse(payload), market="KRW-BTC", approved_amount_krw=Decimal("50000")
        )
    assert caught.value.reason_code == "INSUFFICIENT_FEE_RESERVE"


@pytest.mark.parametrize(
    ("amount", "expected_reason"),
    [
        ("4999.9999", "BELOW_EXCHANGE_MINIMUM"),
        ("1000000000.0001", "ABOVE_EXCHANGE_MAXIMUM"),
        ("100000.0001", "INSUFFICIENT_QUOTE_BALANCE"),
    ],
)
def test_buy_preflight_rejects_min_max_and_available(amount, expected_reason) -> None:
    with pytest.raises(UpbitOrderChanceValidationError) as caught:
        UpbitOrderChancePreflightService().validate_buy(
            parse(), market="KRW-BTC", approved_amount_krw=Decimal(amount)
        )
    assert caught.value.reason_code == expected_reason


@pytest.mark.parametrize("amount", [Decimal("5000"), Decimal("1000000000")])
def test_buy_preflight_accepts_inclusive_min_and_max(amount: Decimal) -> None:
    payload = chance_payload()
    payload["bid_account"]["balance"] = str(amount * Decimal("1.0005"))  # type: ignore[index]
    UpbitOrderChancePreflightService().validate_buy(
        parse(payload), market="KRW-BTC", approved_amount_krw=amount
    )


def test_preflight_ignores_deprecated_order_types() -> None:
    payload = chance_payload()
    payload["market"]["bid_types"] = []  # type: ignore[index]
    payload["market"]["ask_types"] = []  # type: ignore[index]
    chance = parse(payload)
    service = UpbitOrderChancePreflightService()
    with pytest.raises(UpbitOrderChanceValidationError) as buy:
        service.validate_buy(
            chance, market="KRW-BTC", approved_amount_krw=Decimal("5000")
        )
    with pytest.raises(UpbitOrderChanceValidationError) as sell:
        service.validate_sell(
            chance,
            market="KRW-BTC",
            approved_quantity=Decimal("0.0001"),
            current_value_krw=Decimal("10000"),
        )
    assert buy.value.reason_code == "UNSUPPORTED_BUY_ORDER_TYPE"
    assert sell.value.reason_code == "UNSUPPORTED_SELL_ORDER_TYPE"


def test_sell_preflight_validates_available_quantity_and_current_minimum() -> None:
    service = UpbitOrderChancePreflightService(now_fn=lambda: NOW)
    result = service.validate_sell(
        parse(),
        market="KRW-BTC",
        approved_quantity=Decimal("0.01"),
        current_value_krw=Decimal("5000"),
    )
    assert result.audit["approved_quantity"] == "0.01"
    assert result.audit["fee_rate"] == "0.0005"

    with pytest.raises(UpbitOrderChanceValidationError) as balance:
        service.validate_sell(
            parse(),
            market="KRW-BTC",
            approved_quantity=Decimal("0.01000001"),
            current_value_krw=Decimal("5000"),
        )
    assert balance.value.reason_code == "INSUFFICIENT_BASE_BALANCE"

    with pytest.raises(UpbitOrderChanceValidationError) as minimum:
        service.validate_sell(
            parse(),
            market="KRW-BTC",
            approved_quantity=Decimal("0.00001"),
            current_value_krw=Decimal("4999.9999"),
        )
    assert minimum.value.reason_code == "BELOW_EXCHANGE_MINIMUM"
