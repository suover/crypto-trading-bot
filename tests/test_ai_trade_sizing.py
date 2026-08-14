from decimal import Decimal

import pytest

from crypto_trading_bot.ai.trade_advisor import TRADE_ADVICE_SCHEMA, AiTradeAdvice
from crypto_trading_bot.services.ai_trade_recommendation_service import (
    AiTradeRecommendationService,
)


def advice(
    action: str, ratio: Decimal | None, confidence: str = "0.9"
) -> AiTradeAdvice:
    return AiTradeAdvice(
        action=action,
        exchange="UPBIT",
        market="KRW-BTC",
        trade_ratio=ratio,
        confidence=Decimal(confidence),
        recommended_amount_krw=None,
        recommended_quantity=None,
        reason="reason",
        risk_notes="risk",
        primary_factors=[],
        alternatives_considered=[],
        raw_response={"action": action, "trade_ratio": str(ratio)},
    )


def candidate(
    *,
    krw: str = "20000",
    coin: str = "2",
    price: object = "10000",
    maximum: str = "12000",
) -> dict[str, object]:
    return {
        "exchange": "UPBIT",
        "market": "KRW-BTC",
        "enough_candles": True,
        "quote_balance_krw": krw,
        "coin_balance": coin,
        "latest_price": price,
        "minimum_order_amount_krw": "5000",
        "max_order_amount_krw": maximum,
    }


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(Decimal("1"), Decimal("12000")), (Decimal("0.5"), Decimal("10000"))],
)
def test_buy_is_sized_from_balance_and_capped(
    ratio: Decimal, expected: Decimal
) -> None:
    service = object.__new__(AiTradeRecommendationService)
    result = service._apply_safety_rules(advice("BUY", ratio), [candidate()])
    assert result.action == "BUY"
    assert result.trade_ratio == ratio
    assert result.recommended_amount_krw == expected
    assert result.recommended_quantity is None


@pytest.mark.parametrize(
    ("ratio", "expected_quantity"),
    [(Decimal("1"), Decimal("2")), (Decimal("0.25"), Decimal("0.50"))],
)
def test_sell_is_sized_from_available_quantity(
    ratio: Decimal, expected_quantity: Decimal
) -> None:
    service = object.__new__(AiTradeRecommendationService)
    result = service._apply_safety_rules(advice("SELL", ratio), [candidate()])
    assert result.action == "SELL"
    assert result.recommended_quantity == expected_quantity
    assert result.recommended_amount_krw == expected_quantity * Decimal("10000")


@pytest.mark.parametrize(
    ("action", "ratio"),
    [
        ("BUY", None),
        ("BUY", Decimal("-0.1")),
        ("BUY", Decimal("1.1")),
        ("BUY", Decimal("NaN")),
        ("BUY", Decimal("Infinity")),
        ("BUY", Decimal("0")),
        ("SELL", Decimal("0")),
        ("HOLD", Decimal("0.1")),
    ],
)
def test_invalid_action_ratio_combinations_become_hold(
    action: str, ratio: Decimal | None
) -> None:
    service = object.__new__(AiTradeRecommendationService)
    result = service._apply_safety_rules(advice(action, ratio), [candidate()])
    assert result.action == "HOLD"
    assert result.trade_ratio == Decimal("0")
    assert result.recommended_amount_krw is None
    assert result.recommended_quantity is None


def test_hold_zero_ratio_is_valid() -> None:
    service = object.__new__(AiTradeRecommendationService)
    result = service._apply_safety_rules(advice("HOLD", Decimal("0")), [candidate()])
    assert result.action == "HOLD"
    assert result.trade_ratio == Decimal("0")


def test_below_minimum_buy_and_sell_become_hold() -> None:
    service = object.__new__(AiTradeRecommendationService)
    buy = service._apply_safety_rules(
        advice("BUY", Decimal("0.1")), [candidate(krw="10000")]
    )
    sell = service._apply_safety_rules(
        advice("SELL", Decimal("0.1")), [candidate(coin="1", price="10000")]
    )
    assert buy.action == "HOLD"
    assert sell.action == "HOLD"


@pytest.mark.parametrize("price", [None, "bad", "0", "-1", "NaN", "Infinity"])
def test_invalid_sell_price_becomes_hold(price: object) -> None:
    service = object.__new__(AiTradeRecommendationService)
    result = service._apply_safety_rules(
        advice("SELL", Decimal("1")), [candidate(price=price)]
    )
    assert result.action == "HOLD"


def test_confidence_does_not_determine_sizing() -> None:
    service = object.__new__(AiTradeRecommendationService)
    low = service._apply_safety_rules(
        advice("BUY", Decimal("0.5"), "0.1"), [candidate()]
    )
    high = service._apply_safety_rules(
        advice("BUY", Decimal("0.5"), "0.99"), [candidate()]
    )
    assert low.recommended_amount_krw == high.recommended_amount_krw == Decimal("10000")


def test_raw_schema_contains_ratio_and_no_exact_sizing_fields() -> None:
    properties = TRADE_ADVICE_SCHEMA["properties"]
    assert "trade_ratio" in properties
    assert "recommended_amount_krw" not in properties
    assert "recommended_quantity" not in properties
    assert TRADE_ADVICE_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    ("used", "expected_action", "expected_amount"),
    [
        ("0", "BUY", Decimal("20000")),
        ("23000", "BUY", Decimal("7000")),
        ("25000", "BUY", Decimal("5000")),
        ("25000.01", "HOLD", None),
        ("30000", "HOLD", None),
    ],
)
def test_buy_sizing_applies_remaining_daily_capacity(
    monkeypatch: pytest.MonkeyPatch,
    used: str,
    expected_action: str,
    expected_amount: Decimal | None,
) -> None:
    monkeypatch.setenv("DAILY_MAX_ORDER_AMOUNT_KRW", "30000")
    from crypto_trading_bot.config.settings import get_settings

    get_settings.cache_clear()
    service = object.__new__(AiTradeRecommendationService)
    service._get_today_buy_amount_krw = lambda **_: Decimal(used)
    result = service._apply_safety_rules(
        advice("BUY", Decimal("0.5")),
        [candidate(krw="40000", maximum="50000")],
        user_id=1,
        execution_mode="MOCK",
    )
    assert result.action == expected_action
    assert result.trade_ratio == (
        Decimal("0.5") if expected_action == "BUY" else Decimal("0")
    )
    assert result.recommended_amount_krw == expected_amount
    get_settings.cache_clear()


def test_sell_does_not_read_daily_buy_capacity() -> None:
    service = object.__new__(AiTradeRecommendationService)
    service._get_today_buy_amount_krw = lambda **_: pytest.fail(
        "SELL queried BUY capacity"
    )
    result = service._apply_safety_rules(
        advice("SELL", Decimal("1")), [candidate()], user_id=1, execution_mode="LIVE"
    )
    assert result.action == "SELL"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bad", Decimal("0")),
        (Decimal("NaN"), Decimal("0")),
        (Decimal("Infinity"), Decimal("0")),
        (Decimal("-Infinity"), Decimal("0")),
        (Decimal("0"), Decimal("0")),
        (Decimal("0.4"), Decimal("0.4")),
        (Decimal("1"), Decimal("1")),
    ],
)
def test_confidence_is_safely_normalized(value: object, expected: Decimal) -> None:
    service = object.__new__(AiTradeRecommendationService)
    item = advice("HOLD", Decimal("0"))
    object.__setattr__(item, "confidence", value)
    result = service._apply_safety_rules(item, [candidate()])
    assert result.confidence == expected


def test_recommendation_daily_buy_query_isolates_side_and_execution_mode() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE order_logs (user_id INTEGER, trading_mode TEXT, side TEXT, "
            "status TEXT, amount_krw NUMERIC, created_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO order_logs VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "MOCK", "BUY", "MOCK_FILLED", 5000, now),
                (1, "MOCK", "SELL", "MOCK_FILLED", 20000, now),
                (1, "LIVE", "BUY", "LIVE_DONE", 7000, now),
                (1, "LIVE", "SELL", "LIVE_DONE", 30000, now),
            ],
        )
    with Session(engine) as session:
        service = object.__new__(AiTradeRecommendationService)
        service.session = session
        assert service._get_today_buy_amount_krw(1, "MOCK") == Decimal("5000")
        assert service._get_today_buy_amount_krw(1, "LIVE") == Decimal("7000")
