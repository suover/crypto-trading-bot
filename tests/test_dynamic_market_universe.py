from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.analysis.market_ranking import MarketRankingPolicy
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


class RecordingRanking:
    def __init__(self, scores: dict[str, Decimal]) -> None:
        self.scores = scores
        self.seen_markets: list[str] = []

    def rank(self, candidates: list[dict[str, object]]) -> list[dict[str, object]]:
        self.seen_markets = [str(candidate["market"]) for candidate in candidates]
        return sorted(
            (
                {**candidate, "score": self.scores[str(candidate["market"])]}
                for candidate in candidates
            ),
            key=lambda candidate: Decimal(candidate["score"]),
            reverse=True,
        )


class StubUniverseService(MarketUniverseService):
    balances: dict[str, dict[str, Decimal]]
    timeframe_features_by_market: dict[str, dict[str, dict[str, object]]] | None

    def _load_balances(self, *args: object) -> dict[str, dict[str, Decimal]]:
        return self.balances

    def _load_orderbooks(self, markets: list[str]) -> dict[str, dict[str, object]]:
        return {
            market: {"available": True, "spread_rate": "0.001"} for market in markets
        }

    def _timeframe_features(
        self, market: str, collection_status: dict[str, dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        if getattr(self, "timeframe_features_by_market", None) is not None:
            return self.timeframe_features_by_market[market]
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
    ranking_policy: MarketRankingPolicy | None = None,
    timeframe_features_by_market: (
        dict[str, dict[str, dict[str, object]]] | None
    ) = None,
    **setting_overrides: object,
) -> StubUniverseService:
    setting_values: dict[str, object] = {
        "database_url": "postgresql://test:test@localhost/test",
        "market_universe_mode": "DYNAMIC",
        "market_universe_top_n": 1,
        "market_universe_prefilter_n": 2,
        "market_universe_min_24h_trade_value_krw": Decimal("100"),
        "analysis_timeframes": "15m",
    }
    setting_values.update(setting_overrides)
    settings = Settings(
        **setting_values,
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
        ranking_policy=ranking_policy or RankingByLiquidity(),
    )
    service.balances = balances
    service.timeframe_features_by_market = timeframe_features_by_market
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
    position = rows["KRW-ETH"].feature_data["position"]
    assert position["market"] == "KRW-ETH"
    assert position["base_asset"] == "ETH"
    assert position["estimated_cost_basis_krw"] == "160"
    assert position["unrealized_pnl_krw"] == "40"
    assert position["unrealized_pnl_percentage"] == "25.00"


def test_portfolio_coingecko_mapping_does_not_affect_universe_ranking() -> None:
    service = build_service(
        markets=[descriptor("KRW-BTC"), descriptor("KRW-XRP")],
        tickers=[ticker("KRW-BTC", "1000"), ticker("KRW-XRP", "900")],
        balances={"KRW": balance("10000")},
        portfolio_coingecko_asset_mapping="APENFT=apenft,QI=qiswap",
    )

    result = service.build_and_persist()

    assert [candidate.market for candidate in result.candidates] == ["KRW-BTC"]
    assert result.candidates[0].selection_source == "RANKED"
    assert result.candidates[0].buy_eligible is True


def test_holding_outside_prefilter_is_collected_but_not_ranked() -> None:
    ranking = RecordingRanking(
        {
            "KRW-BTC": Decimal("1"),
            "KRW-XRP": Decimal("2"),
            "KRW-ETH": Decimal("999"),
        }
    )
    service = build_service(
        markets=[
            descriptor("KRW-BTC"),
            descriptor("KRW-XRP"),
            descriptor("KRW-ETH"),
        ],
        tickers=[
            ticker("KRW-BTC", "1000"),
            ticker("KRW-XRP", "900"),
            ticker("KRW-ETH", "100"),
        ],
        balances={
            "KRW": balance("10000"),
            "XRP": balance("2"),
            "ETH": balance("2"),
        },
        ranking_policy=ranking,
    )

    result = service.build_and_persist()

    rows = {row.market: row for row in result.candidates}
    assert ranking.seen_markets == ["KRW-BTC", "KRW-XRP"]
    assert rows["KRW-XRP"].selection_source == "RANKED"
    assert rows["KRW-XRP"].buy_eligible is True
    assert rows["KRW-XRP"].sell_eligible is True
    assert rows["KRW-ETH"].selection_source == "HELD"
    assert rows["KRW-ETH"].rank is None
    assert rows["KRW-ETH"].score is None
    assert rows["KRW-ETH"].buy_eligible is False
    assert rows["KRW-ETH"].sell_eligible is True
    assert rows["KRW-ETH"].feature_data["buy_eligible"] is False
    assert result.prefilter_count == 2
    assert result.data_collection_count == 3
    collected_markets = (
        service.candle_service.collect_timeframes_for_markets.call_args.args[0]
    )
    assert set(collected_markets) == {"KRW-BTC", "KRW-XRP", "KRW-ETH"}


def test_ranking_requires_any_sufficient_timeframe_but_keeps_holding() -> None:
    insufficient = {
        "15m": {"data_quality": "INSUFFICIENT", "candle_count": 10},
        "1d": {"data_quality": "INSUFFICIENT", "candle_count": 10},
    }
    partial = {
        "15m": {"data_quality": "SUFFICIENT", "candle_count": 50},
        "1d": {"data_quality": "INSUFFICIENT", "candle_count": 10},
    }
    ranking = RecordingRanking({"KRW-XRP": Decimal("1")})
    service = build_service(
        markets=[
            descriptor("KRW-BTC"),
            descriptor("KRW-XRP"),
            descriptor("KRW-ETH"),
        ],
        tickers=[
            ticker("KRW-BTC", "1000"),
            ticker("KRW-XRP", "900"),
            ticker("KRW-ETH", "100"),
        ],
        balances={"KRW": balance("10000"), "ETH": balance("2")},
        ranking_policy=ranking,
        timeframe_features_by_market={
            "KRW-BTC": insufficient,
            "KRW-XRP": partial,
            "KRW-ETH": insufficient,
        },
        analysis_timeframes="15m,1d",
    )

    result = service.build_and_persist()

    rows = {row.market: row for row in result.candidates}
    assert ranking.seen_markets == ["KRW-XRP"]
    assert set(rows) == {"KRW-XRP", "KRW-ETH"}
    assert rows["KRW-XRP"].selection_source == "RANKED"
    assert rows["KRW-XRP"].feature_data["data_quality"] == "PARTIAL"
    assert rows["KRW-ETH"].selection_source == "HELD"
    assert rows["KRW-ETH"].buy_eligible is False
    assert rows["KRW-ETH"].sell_eligible is True
    assert rows["KRW-ETH"].feature_data["enough_candles"] is False


def test_trading_unsupported_holding_stays_held_and_ineligible() -> None:
    service = build_service(
        markets=[descriptor("KRW-BTC")],
        tickers=[ticker("KRW-BTC", "1000")],
        balances={"KRW": balance("10000"), "DELISTED": balance("2")},
    )

    result = service.build_and_persist()

    rows = {row.market: row for row in result.candidates}
    unsupported = rows["KRW-DELISTED"]
    assert unsupported.selection_source == "HELD"
    assert unsupported.buy_eligible is False
    assert unsupported.sell_eligible is False
    assert unsupported.feature_data["buy_eligible"] is False
    assert unsupported.feature_data["sell_eligible"] is False


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


def test_balance_reader_accepts_new_and_legacy_account_run_types() -> None:
    settings = Settings(database_url="postgresql://test:test@localhost/test")
    session = MagicMock()
    session.scalars.return_value = [
        SimpleNamespace(
            currency="KRW",
            balance=Decimal("1000"),
            locked=Decimal("10"),
            avg_buy_price=Decimal("0"),
        )
    ]
    provider = MagicMock(exchange_code="UPBIT")
    service = MarketUniverseService(
        session,
        settings=settings,
        market_data_provider=provider,
        registry_service=MagicMock(),
        candle_service=MagicMock(),
    )

    balances = service._load_balances(
        1, "UPBIT", "11111111-1111-1111-1111-111111111111"
    )

    statement = session.scalars.call_args.args[0]
    expanding_values = [
        value
        for value in statement.compile().params.values()
        if isinstance(value, list)
    ]
    assert ["ACCOUNT_SNAPSHOT", "MANUAL"] in expanding_values
    assert balances["KRW"]["total"] == Decimal("1010")
