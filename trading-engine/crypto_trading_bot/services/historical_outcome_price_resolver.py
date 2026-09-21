from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from crypto_trading_bot.exchange.market_data import ExchangeMarketDataProvider
from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)


HISTORICAL_CANDLE_COUNT = 10


class HistoricalOutcomePriceResolver:
    """Resolve fully closed historical candles with an exact per-instance cache."""

    def __init__(
        self, market_data_provider: ExchangeMarketDataProvider | None = None
    ) -> None:
        self.provider = market_data_provider or UpbitMarketDataProvider()
        self._cache: dict[
            tuple[str, str, datetime], tuple[Decimal, datetime] | None
        ] = {}

    @property
    def exchange_code(self) -> str:
        return self.provider.exchange_code

    def clear_cache(self) -> None:
        self._cache.clear()

    def resolve(
        self, *, exchange: str, market: str, target_at: datetime
    ) -> tuple[Decimal, datetime] | None:
        normalized_exchange = exchange.strip().upper()
        key = (normalized_exchange, market, target_at)
        if key in self._cache:
            return self._cache[key]
        if normalized_exchange != self.exchange_code:
            self._cache[key] = None
            return None
        try:
            rows = self.provider.get_minute_candles(
                market, unit=1, count=HISTORICAL_CANDLE_COUNT, to=target_at
            )
        except Exception:
            rows = []
        eligible: list[tuple[datetime, Decimal]] = []
        for row in rows:
            candle_at = self.parse_upbit_utc(row.get("candle_date_time_utc"))
            price = self.positive_decimal(row.get("trade_price"))
            if (
                candle_at is not None
                and price is not None
                and candle_at + timedelta(minutes=1) <= target_at
            ):
                eligible.append((candle_at, price))
        selected = max(eligible, default=None, key=lambda item: item[0])
        result = (selected[1], selected[0]) if selected else None
        self._cache[key] = result
        return result

    @staticmethod
    def positive_decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() and parsed > 0 else None

    @staticmethod
    def aware_utc(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC)

    @staticmethod
    def parse_upbit_utc(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
