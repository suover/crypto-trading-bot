from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.indicators import (
    calculate_atr,
    calculate_macd,
    calculate_max_drawdown,
    calculate_realized_volatility,
)
from crypto_trading_bot.db.models import MarketCandle
from crypto_trading_bot.services.market_candle_service import MarketCandleService


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(type_: JSONB, compiler: object, **kwargs: object) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def compile_bigint_for_sqlite(
    type_: BigInteger, compiler: object, **kwargs: object
) -> str:
    return "INTEGER"


class CandleProvider:
    exchange_code = "UPBIT"

    def __init__(self) -> None:
        self.fail_day = False

    @staticmethod
    def _rows(count: int) -> list[dict[str, object]]:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            {
                "candle_date_time_utc": (start + timedelta(minutes=index)).isoformat(),
                "opening_price": "10",
                "high_price": "11",
                "low_price": "9",
                "trade_price": str(10 + index),
                "candle_acc_trade_price": "1000",
                "candle_acc_trade_volume": "100",
            }
            for index in range(count)
        ]

    def get_minute_candles(
        self, market: str, unit: int, count: int
    ) -> list[dict[str, object]]:
        return self._rows(count)

    def get_day_candles(self, market: str, count: int) -> list[dict[str, object]]:
        if self.fail_day:
            raise RuntimeError("day unavailable")
        return self._rows(count)


def candle_session(*, autoflush: bool = True) -> Session:
    engine = create_engine("sqlite://")
    MarketCandle.__table__.create(engine)
    return Session(engine, autoflush=autoflush)


def test_multi_timeframe_candles_save_once_for_15_60_240_and_day() -> None:
    session = candle_session()
    provider = CandleProvider()
    service = MarketCandleService(
        session,
        market_data_provider=provider,
        registry_service=SimpleNamespace(),
    )

    first = service.collect_timeframes_for_markets(
        ["KRW-BTC"], ["15m", "60m", "240m", "1d"], 1
    )
    second = service.collect_timeframes_for_markets(
        ["KRW-BTC"], ["15m", "60m", "240m", "1d"], 1
    )

    rows = list(session.scalars(select(MarketCandle)))
    assert {(row.candle_type, row.candle_unit) for row in rows} == {
        ("MINUTE", 15),
        ("MINUTE", 60),
        ("MINUTE", 240),
        ("DAY", 1),
    }
    assert all(value["saved_count"] == 1 for value in first["KRW-BTC"].values())
    assert all(value["saved_count"] == 0 for value in second["KRW-BTC"].values())


def test_commit_false_flushes_candles_when_session_autoflush_is_disabled() -> None:
    session = candle_session(autoflush=False)
    service = MarketCandleService(
        session,
        market_data_provider=CandleProvider(),
        registry_service=SimpleNamespace(),
    )

    service.collect_timeframes_for_markets(["KRW-BTC"], ["15m"], 20, commit=False)

    assert len(list(session.scalars(select(MarketCandle)))) == 20


def test_timeframe_failure_is_isolated_and_reported() -> None:
    session = candle_session()
    provider = CandleProvider()
    provider.fail_day = True
    service = MarketCandleService(
        session,
        market_data_provider=provider,
        registry_service=SimpleNamespace(),
    )

    result = service.collect_timeframes_for_markets(["KRW-BTC"], ["15m", "1d"], 20)[
        "KRW-BTC"
    ]

    assert result["15m"]["status"] == "AVAILABLE"
    assert result["1d"] == {
        "status": "UNAVAILABLE",
        "received_count": 0,
        "saved_count": 0,
        "error_type": "RuntimeError",
    }


def test_empty_timeframe_response_is_not_treated_as_available() -> None:
    session = candle_session()
    provider = CandleProvider()
    provider.get_day_candles = lambda market, count: []  # type: ignore[method-assign]
    service = MarketCandleService(
        session,
        market_data_provider=provider,
        registry_service=SimpleNamespace(),
    )

    result = service.collect_timeframes_for_markets(["KRW-BTC"], ["1d"], 20)

    assert result["KRW-BTC"]["1d"] == {
        "status": "UNAVAILABLE",
        "received_count": 0,
        "saved_count": 0,
        "error_type": "InvalidCandleResponse",
    }


def test_malformed_timeframe_response_is_not_backfilled_from_stale_rows() -> None:
    session = candle_session()
    provider = CandleProvider()
    service = MarketCandleService(
        session,
        market_data_provider=provider,
        registry_service=SimpleNamespace(),
    )
    service.collect_timeframes_for_markets(["KRW-BTC"], ["1d"], 20)
    provider.get_day_candles = lambda market, count: [  # type: ignore[method-assign]
        {"candle_date_time_utc": "2026-01-02T00:00:00", "trade_price": None}
    ]

    result = service.collect_timeframes_for_markets(["KRW-BTC"], ["1d"], 20)

    assert result["KRW-BTC"]["1d"] == {
        "status": "UNAVAILABLE",
        "received_count": 1,
        "saved_count": 0,
        "error_type": "InvalidCandleResponse",
    }


def test_macd_constant_series_is_zero() -> None:
    macd, signal, histogram = calculate_macd([Decimal("10")] * 40)
    assert macd == Decimal("0")
    assert signal == Decimal("0")
    assert histogram == Decimal("0")


def test_atr_uses_true_range_and_wilder_smoothing() -> None:
    closes = [Decimal("10")] * 20
    assert calculate_atr(
        [Decimal("11")] * 20,
        [Decimal("9")] * 20,
        closes,
    ) == Decimal("2")


def test_realized_volatility_and_max_drawdown() -> None:
    assert calculate_realized_volatility([Decimal("100")] * 21) == Decimal("0")
    assert calculate_max_drawdown(
        [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110")]
    ) == Decimal("25")


@pytest.mark.parametrize("invalid", [[], [Decimal("0"), Decimal("1")]])
def test_max_drawdown_handles_unusable_input(invalid: list[Decimal]) -> None:
    assert calculate_max_drawdown(invalid) is None
