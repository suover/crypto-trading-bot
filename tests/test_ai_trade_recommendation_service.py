from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from crypto_trading_bot.ai.trade_advisor import AiTradeAdvice
from crypto_trading_bot.db.models import ExchangeMarket, MarketCandle
from crypto_trading_bot.services.ai_trade_recommendation_service import (
    AiTradeRecommendationService,
)
from crypto_trading_bot.services.market_data_context_service import (
    MarketDataContextResult,
)


def build_market(market: str, base_asset: str, priority: int) -> ExchangeMarket:
    return ExchangeMarket(
        id=priority,
        exchange_code="UPBIT",
        market=market,
        base_asset=base_asset,
        quote_asset="KRW",
        coingecko_id=base_asset.lower(),
        status="ACTIVE",
        priority=priority,
    )


def build_candles(count: int) -> list[MarketCandle]:
    return [
        MarketCandle(
            exchange="UPBIT",
            market="KRW-TEST",
            candle_type="MINUTE",
            candle_unit=15,
            trade_price=Decimal(100 + index),
            opening_price=Decimal(100 + index),
            high_price=Decimal(101 + index),
            low_price=Decimal(99 + index),
            candle_acc_trade_price=Decimal("1000"),
            candle_acc_trade_volume=Decimal(10 + index),
            raw_data={},
        )
        for index in range(count)
    ]


def build_advice(
    *,
    action: str,
    market: str,
    amount: Decimal | None = None,
    quantity: Decimal | None = None,
) -> AiTradeAdvice:
    raw_response = {
        "action": action,
        "exchange": "UPBIT",
        "market": market,
    }
    return AiTradeAdvice(
        action=action,
        exchange="UPBIT",
        market=market,
        confidence=Decimal("0.9"),
        recommended_amount_krw=amount,
        recommended_quantity=quantity,
        reason="Candidate comparison result.",
        risk_notes="Volatility risk.",
        primary_factors=["trend", "volume"],
        alternatives_considered=[],
        raw_response=raw_response,
    )


class StubRecommendationService(AiTradeRecommendationService):
    candles_by_market: dict[str, list[MarketCandle]]
    balances_by_currency: dict[str, Decimal]

    def _get_user(self, user_name: str) -> SimpleNamespace:
        return SimpleNamespace(id=1)

    def _get_recent_candles(
        self,
        market: str,
        candle_unit: int,
        count: int,
    ) -> list[MarketCandle]:
        return self.candles_by_market[market][:count]

    def _get_latest_balance(
        self,
        user_id: int,
        exchange: str,
        currency: str,
    ) -> Decimal:
        return self.balances_by_currency.get(currency, Decimal("0"))


def build_service(
    *,
    markets: list[ExchangeMarket],
    candles_by_market: dict[str, list[MarketCandle]],
    balances_by_currency: dict[str, Decimal],
    advice: AiTradeAdvice,
    maximums: dict[str, Decimal] | None = None,
) -> tuple[StubRecommendationService, MagicMock, MagicMock, MagicMock]:
    session = MagicMock()
    advisor = MagicMock()
    advisor.model = "test-model"
    advisor.create_advice.return_value = advice
    registry = MagicMock()
    registry.load_allowed_active_markets_for_exchange.return_value = markets
    maximums = maximums or {market.market: Decimal("10000") for market in markets}
    registry.calculate_final_max_order_amount.side_effect = lambda market: maximums[
        market.market
    ]
    market_data = MagicMock()

    def enrich(candidates: list[dict[str, object]]) -> MarketDataContextResult:
        enriched = [
            {
                **candidate,
                "orderbook": {"available": True, "pressure_label": "BALANCED"},
                "global_market": {
                    "available": True,
                    "id": candidate["coingecko_id"],
                },
            }
            for candidate in candidates
        ]
        return MarketDataContextResult(
            candidates=enriched,
            market_sentiment={"available": True, "value": 50},
            external_data_status={
                "upbit_orderbook": "AVAILABLE",
                "coingecko": "AVAILABLE",
                "fear_greed": "AVAILABLE",
            },
        )

    market_data.enrich_candidates.side_effect = enrich
    service = StubRecommendationService(
        session,
        trade_advisor=advisor,
        registry_service=registry,
        market_data_context_service=market_data,
    )
    service.candles_by_market = candles_by_market
    service.balances_by_currency = balances_by_currency
    return service, session, advisor, registry


def test_builds_registry_candidates_and_calls_advisor_once() -> None:
    markets = [
        build_market("KRW-BTC", "BTC", 1),
        build_market("KRW-ETH", "ETH", 2),
        build_market("KRW-XRP", "XRP", 3),
    ]
    service, _, advisor, registry = build_service(
        markets=markets,
        candles_by_market={market.market: build_candles(20) for market in markets},
        balances_by_currency={
            "KRW": Decimal("7000"),
            "BTC": Decimal("0"),
            "ETH": Decimal("0"),
            "XRP": Decimal("0"),
        },
        advice=build_advice(
            action="BUY",
            market="KRW-ETH",
            amount=Decimal("20000"),
        ),
        maximums={
            "KRW-BTC": Decimal("10000"),
            "KRW-ETH": Decimal("6000"),
            "KRW-XRP": Decimal("10000"),
        },
    )

    _, recommendations = service.create_ai_recommendations()

    registry.load_allowed_active_markets_for_exchange.assert_called_once_with("UPBIT")
    advisor.create_advice.assert_called_once()
    market_data = service.market_data_context_service
    market_data.enrich_candidates.assert_called_once()
    base_candidates = market_data.enrich_candidates.call_args.args[0]
    assert [candidate["market"] for candidate in base_candidates] == [
        "KRW-BTC",
        "KRW-ETH",
        "KRW-XRP",
    ]
    context = advisor.create_advice.call_args.args[0]
    candidates = context["candidates"]
    assert [candidate["market"] for candidate in candidates] == [
        "KRW-BTC",
        "KRW-ETH",
        "KRW-XRP",
    ]
    assert all("orderbook" in candidate for candidate in candidates)
    assert all("global_market" in candidate for candidate in candidates)
    assert context["market_sentiment"] == {"available": True, "value": 50}
    assert context["external_data_status"]["coingecko"] == "AVAILABLE"
    assert recommendations[0].market == "KRW-ETH"
    assert recommendations[0].recommended_amount_krw == Decimal("6000")
    assert recommendations[0].ai_response["context"] == context
    assert len(recommendations) == 1


def test_ai_context_and_enrichment_contain_only_allowed_market() -> None:
    market = build_market("KRW-BTC", "BTC", 1)
    service, _, advisor, registry = build_service(
        markets=[market],
        candles_by_market={"KRW-BTC": build_candles(20)},
        balances_by_currency={"KRW": Decimal("10000"), "BTC": Decimal("0")},
        advice=build_advice(action="HOLD", market="KRW-BTC"),
    )

    _, recommendations = service.create_ai_recommendations()

    registry.load_allowed_active_markets_for_exchange.assert_called_once_with("UPBIT")
    enrichment_candidates = (
        service.market_data_context_service.enrich_candidates.call_args.args[0]
    )
    assert [candidate["market"] for candidate in enrichment_candidates] == ["KRW-BTC"]
    context = advisor.create_advice.call_args.args[0]
    assert [candidate["market"] for candidate in context["candidates"]] == ["KRW-BTC"]
    assert recommendations[0].market == "KRW-BTC"


def test_unavailable_external_source_does_not_prevent_advisor_call() -> None:
    market = build_market("KRW-BTC", "BTC", 1)
    service, _, advisor, _ = build_service(
        markets=[market],
        candles_by_market={"KRW-BTC": build_candles(20)},
        balances_by_currency={"KRW": Decimal("10000"), "BTC": Decimal("0")},
        advice=build_advice(action="HOLD", market="KRW-BTC"),
    )
    market_data = service.market_data_context_service
    default_enrich = market_data.enrich_candidates.side_effect

    def unavailable(candidates: list[dict[str, object]]) -> MarketDataContextResult:
        default_result = default_enrich(candidates)
        return MarketDataContextResult(
            candidates=default_result.candidates,
            market_sentiment=default_result.market_sentiment,
            external_data_status={
                **default_result.external_data_status,
                "coingecko": "UNAVAILABLE",
            },
        )

    market_data.enrich_candidates.side_effect = unavailable

    _, recommendations = service.create_ai_recommendations()

    advisor.create_advice.assert_called_once()
    assert (
        advisor.create_advice.call_args.args[0]["external_data_status"]["coingecko"]
        == "UNAVAILABLE"
    )
    assert len(recommendations) == 1


def test_market_not_in_candidates_is_overridden_to_hold() -> None:
    market = build_market("KRW-BTC", "BTC", 1)
    service, _, advisor, _ = build_service(
        markets=[market],
        candles_by_market={"KRW-BTC": build_candles(20)},
        balances_by_currency={"KRW": Decimal("10000"), "BTC": Decimal("0")},
        advice=build_advice(action="BUY", market="KRW-DOGE"),
    )

    _, recommendations = service.create_ai_recommendations()

    assert advisor.create_advice.call_count == 1
    recommendation = recommendations[0]
    assert recommendation.action == "HOLD"
    assert recommendation.market == "KRW-BTC"
    assert recommendation.ai_response["safety_override"] is not None


def test_sell_with_zero_selected_coin_balance_is_overridden_to_hold() -> None:
    market = build_market("KRW-XRP", "XRP", 1)
    service, _, _, _ = build_service(
        markets=[market],
        candles_by_market={"KRW-XRP": build_candles(20)},
        balances_by_currency={"KRW": Decimal("10000"), "XRP": Decimal("0")},
        advice=build_advice(
            action="SELL",
            market="KRW-XRP",
            quantity=Decimal("100"),
        ),
    )

    _, recommendations = service.create_ai_recommendations()

    assert recommendations[0].action == "HOLD"
    assert recommendations[0].recommended_quantity is None
    assert recommendations[0].ai_response["safety_override"] is not None


def test_all_candidates_without_enough_candles_create_system_guard_hold() -> None:
    markets = [
        build_market("KRW-BTC", "BTC", 1),
        build_market("KRW-ETH", "ETH", 2),
    ]
    service, _, advisor, _ = build_service(
        markets=markets,
        candles_by_market={
            "KRW-BTC": build_candles(10),
            "KRW-ETH": build_candles(0),
        },
        balances_by_currency={
            "KRW": Decimal("10000"),
            "BTC": Decimal("0"),
            "ETH": Decimal("0"),
        },
        advice=build_advice(action="BUY", market="KRW-BTC"),
    )

    _, recommendations = service.create_ai_recommendations()

    advisor.create_advice.assert_not_called()
    assert len(recommendations) == 1
    assert recommendations[0].action == "HOLD"
    assert recommendations[0].market == "KRW-BTC"
    assert recommendations[0].ai_response["source"] == "system_guard"
    candidates = recommendations[0].ai_response["context"]["candidates"]
    assert all(candidate["enough_candles"] is False for candidate in candidates)
    assert all("orderbook" in candidate for candidate in candidates)
    service.market_data_context_service.enrich_candidates.assert_called_once()


def test_buy_amount_is_also_capped_by_krw_balance() -> None:
    market = build_market("KRW-BTC", "BTC", 1)
    service, _, _, _ = build_service(
        markets=[market],
        candles_by_market={"KRW-BTC": build_candles(20)},
        balances_by_currency={"KRW": Decimal("7000"), "BTC": Decimal("0")},
        advice=build_advice(
            action="BUY",
            market="KRW-BTC",
            amount=Decimal("20000"),
        ),
        maximums={"KRW-BTC": Decimal("10000")},
    )

    _, recommendations = service.create_ai_recommendations()

    assert recommendations[0].action == "BUY"
    assert recommendations[0].recommended_amount_krw == Decimal("7000")
