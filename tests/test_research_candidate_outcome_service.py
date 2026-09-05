from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from crypto_trading_bot.services.historical_outcome_price_resolver import (
    HistoricalOutcomePriceResolver,
)
from crypto_trading_bot.services.research_candidate_outcome_service import (
    END_PRICE_SOURCE,
    REFERENCE_PRICE_SOURCE,
    ResearchCandidateOutcomeService,
)


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


class FakeProvider:
    exchange_code = "UPBIT"

    def __init__(self, end_price="110") -> None:
        self.end_price = end_price
        self.calls = 0

    def get_minute_candles(self, market, unit, count, to=None):
        self.calls += 1
        return [
            {
                "candle_date_time_utc": (to - timedelta(minutes=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                ),
                "trade_price": self.end_price,
            }
        ]


def candidate(price="100", *, exchange="UPBIT", held=False, in_prefilter=True):
    return SimpleNamespace(
        id=10,
        strategy_replay_snapshot_id=5,
        user_id=1,
        exchange=exchange,
        market="KRW-BTC",
        in_prefilter=in_prefilter,
        held=held,
        feature_data={"latest_price": price, "enough_candles": True},
    )


def snapshot(captured_at=NOW - timedelta(hours=2)):
    return SimpleNamespace(
        id=5,
        user_id=1,
        exchange="UPBIT",
        captured_at=captured_at,
        dataset_schema_version="strategy-replay-dataset-v1",
    )


def values(price="100", *, exchange="UPBIT", captured_at=None):
    provider = FakeProvider()
    service = ResearchCandidateOutcomeService(
        None, price_resolver=HistoricalOutcomePriceResolver(provider)
    )
    snap = snapshot(captured_at or NOW - timedelta(hours=2))
    snap.exchange = exchange
    result = service._values(
        candidate=candidate(price, exchange=exchange),
        snapshot=snap,
        snapshot_at=snap.captured_at,
        target_at=snap.captured_at + timedelta(minutes=60),
        horizon=60,
        now=NOW,
    )
    return result, provider


def test_frozen_reference_and_snapshot_timestamp_define_decimal_return() -> None:
    result, provider = values("100")
    assert result["reference_at"] == snapshot().captured_at
    assert result["target_at"] == snapshot().captured_at + timedelta(minutes=60)
    assert result["reference_price"] == Decimal("100")
    assert result["reference_price_source"] == REFERENCE_PRICE_SOURCE
    assert result["end_price"] == Decimal("110")
    assert result["end_price_source"] == END_PRICE_SOURCE
    assert result["market_return_percentage"] == Decimal("10.0")
    assert result["evaluation_status"] == "COMPLETE"
    assert provider.calls == 1


@pytest.mark.parametrize("price", [None, "0", "-1", "bad", "NaN", "Infinity"])
def test_invalid_frozen_reference_is_nonretryable_partial_without_api(price) -> None:
    result, provider = values(price)
    assert result["evaluation_status"] == "PARTIAL"
    assert result["safe_reason"] == "REFERENCE_PRICE_UNAVAILABLE"
    assert provider.calls == 0


def test_unsupported_exchange_is_partial_without_api() -> None:
    result, provider = values(exchange="BINANCE")
    assert result["safe_reason"] == "UNSUPPORTED_EXCHANGE"
    assert provider.calls == 0


@pytest.mark.parametrize(
    "captured_at",
    [
        datetime(2026, 9, 6, 10, 13, tzinfo=UTC),
        datetime(2026, 9, 6, 14, 47, tzinfo=UTC),
        datetime(2026, 9, 6, 23, 2, tzinfo=UTC),
    ],
)
def test_targets_depend_only_on_arbitrary_snapshot_time(captured_at) -> None:
    service = ResearchCandidateOutcomeService(None)
    assert [
        captured_at + timedelta(minutes=horizon) for horizon in (60, 240, 1440)
    ] == [
        captured_at + timedelta(hours=1),
        captured_at + timedelta(hours=4),
        captured_at + timedelta(days=1),
    ]
    assert service.now_fn is not None


def test_held_candidate_is_valid_when_also_in_prefilter() -> None:
    row = candidate(held=True, in_prefilter=True)
    assert row.held is True
    assert row.in_prefilter is True
