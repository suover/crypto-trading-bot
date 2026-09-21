from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from crypto_trading_bot.services.recommendation_outcome_service import (
    RecommendationOutcomeService,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class FakeProvider:
    exchange_code = "UPBIT"

    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = 0

    def get_minute_candles(self, market, unit, count, to=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


def candidate(price: object = "100", market: str = "KRW-BTC"):
    return SimpleNamespace(
        id=10,
        user_id=1,
        exchange="UPBIT",
        market=market,
        rank=1,
        score=Decimal("0.8"),
        selection_source="RANKED",
        buy_eligible=True,
        sell_eligible=False,
        feature_data={"latest_price": price, "held": False},
    )


def recommendation(action: str = "BUY", market: str = "KRW-BTC"):
    return SimpleNamespace(
        id=20,
        user_id=1,
        exchange="UPBIT",
        market=market,
        action=action,
        universe_candidate_id=10,
    )


def service(rows=None, error=None):
    return RecommendationOutcomeService(
        None,
        market_data_provider=FakeProvider(rows, error),
        now_fn=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("action", "end", "market_return", "aligned", "direction"),
    [
        ("BUY", "110", Decimal("10.0"), Decimal("10.0"), "WIN"),
        ("BUY", "90", Decimal("-10.0"), Decimal("-10.0"), "LOSS"),
        ("SELL", "90", Decimal("-10.0"), Decimal("10.0"), "WIN"),
        ("SELL", "110", Decimal("10.0"), Decimal("-10.0"), "LOSS"),
        ("BUY", "100", Decimal("0"), Decimal("0"), "FLAT"),
        ("SELL", "100", Decimal("0"), Decimal("0"), "FLAT"),
        ("HOLD", "120", Decimal("20.0"), None, "NOT_APPLICABLE"),
    ],
)
def test_selected_action_semantics(action, end, market_return, aligned, direction):
    target = NOW - timedelta(hours=1)
    rows = [
        {
            "candle_date_time_utc": (target - timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "trade_price": end,
        }
    ]
    values = service(rows)._selected_values(
        recommendation(action), candidate(), NOW - timedelta(hours=2), target, 60, NOW
    )
    assert values["evaluation_status"] == "COMPLETE"
    assert values["market_return_percentage"] == market_return
    assert values["action_aligned_return_percentage"] == aligned
    assert values["directional_result"] == direction


def test_historical_price_uses_only_fully_closed_candle_without_lookahead():
    target = datetime(2026, 9, 5, 10, 0, 30, tzinfo=UTC)
    provider = FakeProvider(
        [
            {"candle_date_time_utc": "2026-09-05T10:00:00", "trade_price": "999"},
            {"candle_date_time_utc": "2026-09-05T09:59:00", "trade_price": "101"},
            {"candle_date_time_utc": "2026-09-05T09:58:00", "trade_price": "100"},
        ]
    )
    outcome = RecommendationOutcomeService(None, market_data_provider=provider)
    assert outcome._historical_price("KRW-BTC", target) == (
        Decimal("101"),
        datetime(2026, 9, 5, 9, 59, tzinfo=UTC),
    )


@pytest.mark.parametrize("price", [None, "NaN", "Infinity", "0", "-1", "bad"])
def test_invalid_reference_price_is_partial_without_market_call(price):
    outcome = service([])
    values = outcome._selected_values(
        recommendation(), candidate(price), NOW - timedelta(hours=2), NOW, 60, NOW
    )
    assert values["evaluation_status"] == "PARTIAL"
    assert values["safe_reason"] == "REFERENCE_PRICE_UNAVAILABLE"
    assert outcome.provider.calls == 0


def test_selected_candidate_mismatch_fails_closed():
    values = service([])._selected_values(
        recommendation(),
        candidate(market="KRW-ETH"),
        NOW - timedelta(hours=2),
        NOW,
        60,
        NOW,
    )
    assert values["evaluation_status"] == "PARTIAL"
    assert values["safe_reason"] == "SELECTED_CANDIDATE_MISMATCH"


def test_public_api_failure_is_partial_and_cached_per_market_target():
    outcome = service(error=TimeoutError())
    target = NOW - timedelta(hours=1)
    first = outcome._candidate_values(
        recommendation(), candidate(), NOW - timedelta(hours=2), target, 60, NOW
    )
    second = outcome._candidate_values(
        recommendation(), candidate(), NOW - timedelta(hours=2), target, 60, NOW
    )
    assert (
        first["safe_reason"] == second["safe_reason"] == "HISTORICAL_PRICE_UNAVAILABLE"
    )
    assert outcome.provider.calls == 1
