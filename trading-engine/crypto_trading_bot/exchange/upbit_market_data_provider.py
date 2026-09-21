from datetime import datetime
from typing import Any

from crypto_trading_bot.exchange.market_data import (
    ExchangeMarketInfo,
    ExchangeTicker,
    optional_decimal,
)
from crypto_trading_bot.exchange.upbit_client import UpbitClient


def _event_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return any(_event_flag(item) for item in value.values())
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "none", "false", "normal", "no"}:
            return False
        if normalized in {"true", "warning", "caution", "yes"}:
            return True
        return True
    return value is not None and bool(value)


class UpbitMarketDataProvider:
    exchange_code = "UPBIT"

    def __init__(self, client: UpbitClient | None = None) -> None:
        self.client = client or UpbitClient()

    def list_markets(self, quote_asset: str | None = None) -> list[ExchangeMarketInfo]:
        target_quote = quote_asset.strip().upper() if quote_asset else None
        result: list[ExchangeMarketInfo] = []
        for row in self.client.get_markets(is_details=True):
            market = row.get("market")
            if not isinstance(market, str):
                continue
            parts = market.split("-", maxsplit=1)
            if len(parts) != 2 or not all(parts):
                continue
            quote, base = (part.upper() for part in parts)
            if target_quote is not None and quote != target_quote:
                continue
            raw_event = row.get("market_event")
            event = raw_event if isinstance(raw_event, dict) else {}
            warning_available = "warning" in event
            caution_available = "caution" in event
            result.append(
                ExchangeMarketInfo(
                    exchange=self.exchange_code,
                    market=market.upper(),
                    base_asset=base,
                    quote_asset=quote,
                    korean_name=(
                        str(row["korean_name"])
                        if row.get("korean_name") is not None
                        else None
                    ),
                    english_name=(
                        str(row["english_name"])
                        if row.get("english_name") is not None
                        else None
                    ),
                    # Dynamic BUY must fail closed when the requested detail fields
                    # are absent; SELL remains independently eligible for holdings.
                    is_warning=(
                        not warning_available or _event_flag(event.get("warning"))
                    ),
                    is_caution=(
                        not caution_available or _event_flag(event.get("caution"))
                    ),
                    market_event=event,
                    raw_data=row,
                )
            )
        return result

    def get_tickers(
        self,
        markets: list[str] | None = None,
        quote_asset: str | None = None,
    ) -> list[ExchangeTicker]:
        if markets is not None:
            rows = self.client.get_tickers(markets)
        else:
            if quote_asset is None:
                raise ValueError("quote_asset is required when markets is not supplied")
            rows = self.client.get_all_tickers(quote_asset)
        result: list[ExchangeTicker] = []
        for row in rows:
            market = row.get("market")
            if not isinstance(market, str) or not market:
                continue
            result.append(
                ExchangeTicker(
                    exchange=self.exchange_code,
                    market=market.upper(),
                    trade_price=optional_decimal(row.get("trade_price")),
                    signed_change_rate=optional_decimal(row.get("signed_change_rate")),
                    quote_trade_value_24h=optional_decimal(
                        row.get("acc_trade_price_24h")
                    ),
                    base_trade_volume_24h=optional_decimal(
                        row.get("acc_trade_volume_24h")
                    ),
                    raw_data=row,
                )
            )
        return result

    def get_orderbooks(
        self, markets: list[str], count: int = 15
    ) -> list[dict[str, Any]]:
        return self.client.get_orderbooks(markets, count=count)

    def get_minute_candles(
        self,
        market: str,
        unit: int,
        count: int,
        to: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        if to is None:
            return self.client.get_minute_candles(
                market=market,
                unit=unit,
                count=count,
            )
        return self.client.get_minute_candles(
            market=market,
            unit=unit,
            count=count,
            to=to,
        )

    def get_day_candles(self, market: str, count: int) -> list[dict[str, Any]]:
        return self.client.get_day_candles(market, count=count)
