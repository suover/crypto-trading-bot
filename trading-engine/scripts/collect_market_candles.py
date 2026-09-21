from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.market_candle_service import MarketCandleService


def collect_market_candles() -> None:
    with SessionLocal() as session:
        service = MarketCandleService(session)

        saved_counts = service.collect_minute_candles(
            unit=15,
            count=50,
        )

        for market, saved_count in saved_counts.items():
            print(
                f"Market candles saved. "
                f"market={market}, "
                f"candle_unit=minutes_15, "
                f"saved_count={saved_count}"
            )


if __name__ == "__main__":
    collect_market_candles()
