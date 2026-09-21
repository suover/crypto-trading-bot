from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.indicators import calculate_market_indicators
from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import MarketCandle


def to_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def format_decimal(value: Decimal | None, digit_count: int = 2) -> str:
    if value is None:
        return "-"

    return f"{value:,.{digit_count}f}"


def get_recent_candles(
    session: Session,
    market: str,
    candle_unit: int,
    count: int,
) -> list[MarketCandle]:
    statement = (
        select(MarketCandle)
        .where(
            MarketCandle.exchange == "UPBIT",
            MarketCandle.market == market,
            MarketCandle.candle_type == "MINUTE",
            MarketCandle.candle_unit == candle_unit,
        )
        .order_by(MarketCandle.candle_at.desc())
        .limit(count)
    )

    candles = list(session.scalars(statement))

    return list(reversed(candles))


def check_market_indicators() -> None:
    settings = get_settings()
    candle_unit = 15
    candle_count = 50

    with SessionLocal() as session:
        for market in settings.allowed_market_list:
            candles = get_recent_candles(
                session=session,
                market=market,
                candle_unit=candle_unit,
                count=candle_count,
            )

            if not candles:
                print(f"{market} | 저장된 캔들 데이터가 없습니다.")
                continue

            close_prices = [to_decimal(candle.trade_price) for candle in candles]
            volumes = [to_decimal(candle.candle_acc_trade_volume) for candle in candles]

            indicators = calculate_market_indicators(
                market=market,
                candle_unit=candle_unit,
                close_prices=close_prices,
                volumes=volumes,
            )

            print("=" * 60)
            print(f"market: {indicators.market}")
            print(f"candle_unit: {indicators.candle_unit}분봉")
            print(f"candle_count: {indicators.candle_count}")
            print(f"latest_price: {format_decimal(indicators.latest_price)}")
            print(f"sma_5: {format_decimal(indicators.sma_5)}")
            print(f"sma_20: {format_decimal(indicators.sma_20)}")
            print(f"ema_5: {format_decimal(indicators.ema_5)}")
            print(f"ema_20: {format_decimal(indicators.ema_20)}")
            print(f"rsi_14: {format_decimal(indicators.rsi_14)}")
            print(
                "recent_10_candle_change_rate: "
                f"{format_decimal(indicators.recent_10_candle_change_rate)}%"
            )
            print(
                "volume_ratio_5_to_20: "
                f"{format_decimal(indicators.volume_ratio_5_to_20)}%"
            )
            print(f"trend_label: {indicators.trend_label}")


if __name__ == "__main__":
    check_market_indicators()
