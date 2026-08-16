from unittest.mock import MagicMock

from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)


def test_market_event_and_null_ticker_parsing_are_safe() -> None:
    client = MagicMock()
    client.get_markets.return_value = [
        {
            "market": "KRW-BTC",
            "market_warning": "CAUTION",
            "market_event": {"warning": False, "caution": {"PRICE": True}},
        },
        {"market": "KRW-ETH", "market_event": None},
        {"market": "BTC-USDT", "market_event": None},
        {"market": None},
    ]
    client.get_all_tickers.return_value = [
        {
            "market": "KRW-BTC",
            "trade_price": None,
            "acc_trade_price_24h": "1234",
            "acc_trade_volume_24h": "999999",
        }
    ]
    provider = UpbitMarketDataProvider(client)

    markets = provider.list_markets("KRW")
    tickers = provider.get_tickers(quote_asset="KRW")

    client.get_markets.assert_called_once_with(is_details=True)
    client.get_all_tickers.assert_called_once_with("KRW")
    assert len(markets) == 2
    assert markets[0].is_warning is False
    assert markets[0].is_caution is True
    assert markets[1].is_warning is True
    assert markets[1].is_caution is True
    assert tickers[0].trade_price is None
    assert str(tickers[0].quote_trade_value_24h) == "1234"
    assert str(tickers[0].base_trade_volume_24h) == "999999"
