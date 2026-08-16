from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


def optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return None
    return result if result.is_finite() else None


@dataclass(frozen=True)
class ExchangeMarketInfo:
    exchange: str
    market: str
    base_asset: str
    quote_asset: str
    korean_name: str | None
    english_name: str | None
    is_warning: bool
    is_caution: bool
    market_event: dict[str, Any]
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class ExchangeTicker:
    exchange: str
    market: str
    trade_price: Decimal | None
    signed_change_rate: Decimal | None
    quote_trade_value_24h: Decimal | None
    base_trade_volume_24h: Decimal | None
    raw_data: dict[str, Any]


class ExchangeMarketDataProvider(Protocol):
    @property
    def exchange_code(self) -> str: ...

    def list_markets(
        self, quote_asset: str | None = None
    ) -> list[ExchangeMarketInfo]: ...

    def get_tickers(
        self, markets: list[str] | None = None, quote_asset: str | None = None
    ) -> list[ExchangeTicker]: ...

    def get_orderbooks(
        self, markets: list[str], count: int = 15
    ) -> list[dict[str, Any]]: ...

    def get_minute_candles(
        self, market: str, unit: int, count: int
    ) -> list[dict[str, Any]]: ...

    def get_day_candles(self, market: str, count: int) -> list[dict[str, Any]]: ...
