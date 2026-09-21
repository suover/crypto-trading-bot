from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import MarketCandle
from crypto_trading_bot.exchange.market_data import ExchangeMarketDataProvider
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)
from crypto_trading_bot.services.exchange_market_registry_service import (
    ExchangeMarketRegistryService,
)


def to_decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        raise ValueError("Candle numeric field is invalid") from None
    if not result.is_finite():
        raise ValueError("Candle numeric field must be finite")
    return result


def parse_upbit_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


class MarketCandleService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
        registry_service: ExchangeMarketRegistryService | None = None,
        market_data_provider: ExchangeMarketDataProvider | None = None,
    ) -> None:
        self.session = session
        client = upbit_client or UpbitClient()
        self.upbit_client = client
        self.market_data_provider = market_data_provider or UpbitMarketDataProvider(
            client
        )
        self.registry_service = registry_service or ExchangeMarketRegistryService(
            session
        )

    def collect_minute_candles(
        self,
        unit: int = 15,
        count: int = 50,
    ) -> dict[str, int]:
        active_markets = self.registry_service.load_allowed_active_markets_for_exchange(
            "UPBIT"
        )
        saved_counts: dict[str, int] = {}
        for registry_market in active_markets:
            candles = self.market_data_provider.get_minute_candles(
                market=registry_market.market, unit=unit, count=count
            )
            saved_count, _ = self._save_candles(
                exchange="UPBIT",
                market=registry_market.market,
                candle_type="MINUTE",
                candle_unit=unit,
                candles=candles,
            )
            saved_counts[registry_market.market] = saved_count
        self.session.commit()
        return saved_counts

    def collect_timeframes_for_markets(
        self,
        markets: list[str],
        timeframes: list[str],
        count: int,
        *,
        commit: bool = True,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        results: dict[str, dict[str, dict[str, Any]]] = {}
        for market in markets:
            market_results: dict[str, dict[str, Any]] = {}
            for timeframe in timeframes:
                candle_type, candle_unit = self._timeframe_storage(timeframe)
                try:
                    if candle_type == "DAY":
                        candles = self.market_data_provider.get_day_candles(
                            market, count
                        )
                    else:
                        candles = self.market_data_provider.get_minute_candles(
                            market, candle_unit, count
                        )
                except Exception as error:
                    market_results[timeframe] = {
                        "status": "UNAVAILABLE",
                        "received_count": 0,
                        "saved_count": 0,
                        "error_type": type(error).__name__,
                    }
                    continue
                saved, valid = self._save_candles(
                    exchange=self.market_data_provider.exchange_code,
                    market=market,
                    candle_type=candle_type,
                    candle_unit=candle_unit,
                    candles=candles,
                )
                if valid == 0:
                    market_results[timeframe] = {
                        "status": "UNAVAILABLE",
                        "received_count": len(candles),
                        "saved_count": 0,
                        "error_type": "InvalidCandleResponse",
                    }
                    continue
                market_results[timeframe] = {
                    "status": "AVAILABLE",
                    "received_count": len(candles),
                    "saved_count": saved,
                }
            results[market] = market_results
        if commit:
            self.session.commit()
        else:
            self.session.flush()
        return results

    @staticmethod
    def _timeframe_storage(timeframe: str) -> tuple[str, int]:
        mapping = {
            "15m": ("MINUTE", 15),
            "60m": ("MINUTE", 60),
            "240m": ("MINUTE", 240),
            "1d": ("DAY", 1),
        }
        try:
            return mapping[timeframe]
        except KeyError:
            raise ValueError(f"Unsupported timeframe: {timeframe}") from None

    def _save_candles(
        self,
        *,
        exchange: str,
        market: str,
        candle_type: str,
        candle_unit: int,
        candles: list[dict[str, Any]],
    ) -> tuple[int, int]:
        saved_count = 0
        valid_count = 0
        valid_rows = [
            row
            for row in candles
            if isinstance(row, dict)
            and isinstance(row.get("candle_date_time_utc"), str)
        ]
        for candle in sorted(
            valid_rows, key=lambda item: str(item["candle_date_time_utc"])
        ):
            try:
                candle_at = parse_upbit_utc_datetime(candle["candle_date_time_utc"])
                values = {
                    key: to_decimal(candle.get(key))
                    for key in (
                        "opening_price",
                        "high_price",
                        "low_price",
                        "trade_price",
                        "candle_acc_trade_price",
                        "candle_acc_trade_volume",
                    )
                }
            except TypeError, ValueError:
                continue
            valid_count += 1
            existing_candle = (
                self.session.query(MarketCandle.id)
                .filter(
                    MarketCandle.exchange == exchange,
                    MarketCandle.market == market,
                    MarketCandle.candle_type == candle_type,
                    MarketCandle.candle_unit == candle_unit,
                    MarketCandle.candle_at == candle_at,
                )
                .first()
            )
            if existing_candle is not None:
                continue
            self.session.add(
                MarketCandle(
                    exchange=exchange,
                    market=market,
                    candle_type=candle_type,
                    candle_unit=candle_unit,
                    candle_at=candle_at,
                    raw_data=candle,
                    **values,
                )
            )
            saved_count += 1
        return saved_count, valid_count
