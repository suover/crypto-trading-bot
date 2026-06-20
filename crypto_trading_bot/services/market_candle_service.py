from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import MarketCandle
from crypto_trading_bot.exchange.upbit_client import UpbitClient


def to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def parse_upbit_utc_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


class MarketCandleService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def collect_minute_candles(
        self,
        unit: int = 15,
        count: int = 50,
    ) -> dict[str, int]:
        settings = get_settings()
        saved_counts: dict[str, int] = {}

        for market in settings.allowed_market_list:
            candles = self.upbit_client.get_minute_candles(
                market=market,
                unit=unit,
                count=count,
            )

            saved_count = 0

            for candle in sorted(
                candles,
                key=lambda item: item["candle_date_time_utc"],
            ):
                candle_at = parse_upbit_utc_datetime(
                    candle["candle_date_time_utc"]
                )

                existing_candle = (
                    self.session.query(MarketCandle.id)
                    .filter(
                        MarketCandle.exchange == "UPBIT",
                        MarketCandle.market == market,
                        MarketCandle.candle_type == "MINUTE",
                        MarketCandle.candle_unit == unit,
                        MarketCandle.candle_at == candle_at,
                    )
                    .first()
                )

                if existing_candle is not None:
                    continue

                market_candle = MarketCandle(
                    exchange="UPBIT",
                    market=market,
                    candle_type="MINUTE",
                    candle_unit=unit,
                    candle_at=candle_at,
                    opening_price=to_decimal(candle["opening_price"]),
                    high_price=to_decimal(candle["high_price"]),
                    low_price=to_decimal(candle["low_price"]),
                    trade_price=to_decimal(candle["trade_price"]),
                    candle_acc_trade_price=to_decimal(
                        candle["candle_acc_trade_price"]
                    ),
                    candle_acc_trade_volume=to_decimal(
                        candle["candle_acc_trade_volume"]
                    ),
                    raw_data=candle,
                )

                self.session.add(market_candle)
                saved_count += 1

            saved_counts[market] = saved_count

        self.session.commit()

        return saved_counts