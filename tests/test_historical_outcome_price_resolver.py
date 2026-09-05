from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_trading_bot.services.historical_outcome_price_resolver import (
    HistoricalOutcomePriceResolver,
)


class FakeProvider:
    exchange_code = "UPBIT"

    def __init__(self, rows=None, error=None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = []

    def get_minute_candles(self, market, unit, count, to=None):
        self.calls.append((market, unit, count, to))
        if self.error:
            raise self.error
        return self.rows


def test_resolver_uses_latest_fully_closed_candle_without_lookahead() -> None:
    target = datetime(2026, 9, 6, 10, 0, 30, tzinfo=UTC)
    provider = FakeProvider(
        [
            {"candle_date_time_utc": "2026-09-06T10:01:00", "trade_price": "500"},
            {"candle_date_time_utc": "2026-09-06T10:00:00", "trade_price": "400"},
            {"candle_date_time_utc": "2026-09-06T09:59:00", "trade_price": "110"},
            {"candle_date_time_utc": "2026-09-06T09:58:00", "trade_price": "100"},
        ]
    )
    resolver = HistoricalOutcomePriceResolver(provider)
    assert resolver.resolve(exchange="UPBIT", market="KRW-BTC", target_at=target) == (
        Decimal("110"),
        datetime(2026, 9, 6, 9, 59, tzinfo=UTC),
    )


def test_cache_reuses_only_exact_exchange_market_target_key() -> None:
    target = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    provider = FakeProvider(
        [
            {
                "candle_date_time_utc": "2026-09-06T09:59:00",
                "trade_price": "100",
            }
        ]
    )
    resolver = HistoricalOutcomePriceResolver(provider)
    resolver.resolve(exchange="UPBIT", market="KRW-BTC", target_at=target)
    resolver.resolve(exchange="UPBIT", market="KRW-BTC", target_at=target)
    assert len(provider.calls) == 1
    resolver.resolve(
        exchange="UPBIT", market="KRW-BTC", target_at=target + timedelta(seconds=1)
    )
    resolver.resolve(exchange="UPBIT", market="KRW-ETH", target_at=target)
    assert len(provider.calls) == 3


def test_provider_failure_and_invalid_prices_return_unavailable() -> None:
    target = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    resolver = HistoricalOutcomePriceResolver(FakeProvider(error=TimeoutError()))
    assert (
        resolver.resolve(exchange="UPBIT", market="KRW-BTC", target_at=target) is None
    )
    for value in (None, True, "0", "-1", "NaN", "Infinity", "bad"):
        assert HistoricalOutcomePriceResolver.positive_decimal(value) is None


def test_unsupported_exchange_does_not_call_provider() -> None:
    provider = FakeProvider()
    resolver = HistoricalOutcomePriceResolver(provider)
    assert (
        resolver.resolve(
            exchange="BINANCE",
            market="BTC-USDT",
            target_at=datetime(2026, 9, 6, tzinfo=UTC),
        )
        is None
    )
    assert provider.calls == []
