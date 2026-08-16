from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.exchange.market_data import ExchangeMarketInfo, ExchangeTicker
from crypto_trading_bot.services.market_universe_service import MarketUniverseService


def descriptor(
    market: str, *, warning: bool = False, caution: bool = False
) -> ExchangeMarketInfo:
    quote, base = market.split("-")
    return ExchangeMarketInfo(
        exchange="UPBIT",
        market=market,
        base_asset=base,
        quote_asset=quote,
        korean_name=None,
        english_name=None,
        is_warning=warning,
        is_caution=caution,
        market_event={"warning": warning, "caution": caution},
        raw_data={},
    )


def ticker(market: str, trade_value: str, volume: str = "999999999") -> ExchangeTicker:
    return ExchangeTicker(
        exchange="UPBIT",
        market=market,
        trade_price=Decimal("100"),
        signed_change_rate=Decimal("0.01"),
        quote_trade_value_24h=Decimal(trade_value),
        base_trade_volume_24h=Decimal(volume),
        raw_data={"market": market},
    )


class RankingByLiquidity:
    def rank(self, candidates: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {**candidate, "score": Decimal(candidate["quote_trade_value_24h"]) / 1000}
            for candidate in sorted(
                candidates,
                key=lambda value: Decimal(value["quote_trade_value_24h"]),
                reverse=True,
            )
        ]


class StubUniverseService(MarketUniverseService):
    balances: dict[str, dict[str, Decimal]]

    def _load_balances(self, *args: object) -> dict[str, dict[str, Decimal]]:
        return self.balances

    def _load_orderbooks(self, markets: list[str]) -> dict[str, dict[str, object]]:
        return {
            market: {"available": True, "spread_rate": "0.001"} for market in markets
        }

    def _timeframe_features(
        self, market: str, collection_status: dict[str, dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        return {
            "15m": {
                "data_quality": "SUFFICIENT",
                "candle_count": 50,
                "trend_label": "상승 우위",
                "recent_change_rate": "1",
                "volume_ratio": "100",
                "realized_volatility": "2",
                "max_drawdown": "3",
            }
        }


def build_service(
    *,
    markets: list[ExchangeMarketInfo],
    tickers: list[ExchangeTicker],
    balances: dict[str, dict[str, Decimal]],
    **setting_overrides: object,
) -> StubUniverseService:
    settings = Settings(
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=1,
        market_universe_prefilter_n=2,
        market_universe_min_24h_trade_value_krw=Decimal("100"),
        analysis_timeframes="15m",
        **setting_overrides,
    )
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(id=1)
    provider = MagicMock()
    provider.exchange_code = "UPBIT"
    provider.list_markets.return_value = markets
    provider.get_tickers.return_value = tickers
    candle_service = MagicMock()
    candle_service.collect_timeframes_for_markets.side_effect = (
        lambda target_markets, timeframes, count, **kwargs: {
            market: {"15m": {"status": "AVAILABLE"}} for market in target_markets
        }
    )
    registry = MagicMock()
    registry.get_market.return_value = None
    registry.calculate_default_max_order_amount.return_value = Decimal("10000")
    service = StubUniverseService(
        session,
        settings=settings,
        market_data_provider=provider,
        registry_service=registry,
        candle_service=candle_service,
        ranking_policy=RankingByLiquidity(),
    )
    service.balances = balances
    return service


def balance(value: str) -> dict[str, Decimal]:
    amount = Decimal(value)
    return {
        "balance": amount,
        "locked": Decimal("0"),
        "total": amount,
        "avg": Decimal("80") if amount > 0 else Decimal("0"),
    }


def test_dynamic_discovers_krw_uses_quote_trade_value_and_keeps_warning_holding() -> (
    None
):
    service = build_service(
        markets=[
            descriptor("KRW-BTC"),
            descriptor("KRW-XRP"),
            descriptor("KRW-ETH", warning=True),
            descriptor("BTC-USDT"),
        ],
        tickers=[
            ticker("KRW-BTC", "1000", volume="1"),
            ticker("KRW-XRP", "900", volume="999999999999"),
            ticker("KRW-ETH", "800"),
        ],
        balances={"KRW": balance("10000"), "ETH": balance("2")},
    )

    result = service.build_and_persist()

    assert result.total_market_count == 3
    assert result.warning_excluded_count == 1
    assert result.ranked_count == 1
    assert result.holdings_added_count == 1
    rows = {row.market: row for row in result.candidates}
    assert set(rows) == {"KRW-BTC", "KRW-ETH"}
    assert rows["KRW-BTC"].selection_source == "RANKED"
    assert rows["KRW-BTC"].quote_trade_value_24h == Decimal("1000")
    assert rows["KRW-ETH"].selection_source == "HELD"
    assert rows["KRW-ETH"].buy_eligible is False
    assert rows["KRW-ETH"].sell_eligible is True


def test_dynamic_filters_caution_blocklist_and_minimum_quote_trade_value() -> None:
    service = build_service(
        markets=[
            descriptor("KRW-BTC"),
            descriptor("KRW-XRP", caution=True),
            descriptor("KRW-DOGE"),
            descriptor("KRW-ADA"),
        ],
        tickers=[
            ticker("KRW-BTC", "1000"),
            ticker("KRW-XRP", "900"),
            ticker("KRW-DOGE", "50"),
            ticker("KRW-ADA", "800"),
        ],
        balances={"KRW": balance("10000")},
        market_blocklist="KRW-ADA",
    )

    result = service.build_and_persist()

    assert [row.market for row in result.candidates] == ["KRW-BTC"]
    assert result.caution_excluded_count == 1
    assert result.blocklist_excluded_count == 1
    assert result.liquidity_excluded_count == 1


def test_static_mode_uses_allowed_registry_markets_without_discovery() -> None:
    settings = Settings(
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="STATIC",
        allowed_markets="KRW-BTC,KRW-ETH",
        analysis_timeframes="15m",
    )
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(id=1)
    provider = MagicMock()
    provider.exchange_code = "UPBIT"
    provider.get_tickers.return_value = [
        ticker("KRW-BTC", "1000"),
        ticker("KRW-ETH", "900"),
    ]
    registry = MagicMock()
    registry.load_allowed_active_markets_for_exchange.return_value = [
        SimpleNamespace(
            market="KRW-BTC",
            base_asset="BTC",
            quote_asset="KRW",
            coingecko_id="bitcoin",
        ),
        SimpleNamespace(
            market="KRW-ETH",
            base_asset="ETH",
            quote_asset="KRW",
            coingecko_id="ethereum",
        ),
    ]
    registry.get_market.side_effect = (
        registry.load_allowed_active_markets_for_exchange.return_value
    )
    registry.calculate_final_max_order_amount.return_value = Decimal("10000")
    candle_service = MagicMock()
    candle_service.collect_timeframes_for_markets.return_value = {
        market: {"15m": {"status": "AVAILABLE"}} for market in ("KRW-BTC", "KRW-ETH")
    }
    service = StubUniverseService(
        session,
        settings=settings,
        market_data_provider=provider,
        registry_service=registry,
        candle_service=candle_service,
    )
    service.balances = {"KRW": balance("10000")}

    result = service.build_and_persist()

    provider.list_markets.assert_not_called()
    provider.get_tickers.assert_called_once_with(markets=["KRW-BTC", "KRW-ETH"])
    assert [row.market for row in result.candidates] == ["KRW-BTC", "KRW-ETH"]
    assert all(row.selection_source == "STATIC" for row in result.candidates)


def test_ranking_does_not_reward_missing_risk_metrics() -> None:
    common = {
        "quote_trade_value_24h": "1000",
        "orderbook": {"spread_rate": "0.001"},
        "timeframes": {
            "15m": {
                "data_quality": "SUFFICIENT",
                "trend_label": "관망",
                "recent_change_rate": "0",
                "volume_ratio": "100",
            }
        },
    }
    complete = {
        **common,
        "market": "KRW-BTC",
        "timeframes": {
            "15m": {
                **common["timeframes"]["15m"],
                "realized_volatility": "0",
                "max_drawdown": "0",
            }
        },
    }
    missing = {**common, "market": "KRW-ETH"}

    ranked = HeuristicMarketRankingPolicy().rank([missing, complete])

    assert ranked[0]["market"] == "KRW-BTC"
    assert ranked[0]["score"] > ranked[1]["score"]
