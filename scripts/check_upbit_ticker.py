from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient


def check_upbit_ticker() -> None:
    settings = get_settings()
    client = UpbitClient()

    tickers = client.get_tickers(settings.allowed_market_list)

    for ticker in tickers:
        market = ticker["market"]
        trade_price = ticker["trade_price"]
        signed_change_rate = ticker["signed_change_rate"]

        print(f"{market} | price={trade_price} | change_rate={signed_change_rate}")


if __name__ == "__main__":
    check_upbit_ticker()
