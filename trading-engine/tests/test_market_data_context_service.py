from copy import deepcopy
from unittest.mock import Mock

import pytest

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.market_data_context_service import (
    MarketDataContextService,
)


def settings(**overrides: object) -> Settings:
    return Settings(database_url="postgresql://test:test@localhost/test", **overrides)


def clients() -> tuple[Mock, Mock, Mock]:
    upbit = Mock()
    upbit.get_orderbooks.return_value = [
        {
            "market": "KRW-BTC",
            "timestamp": 123,
            "total_bid_size": "12",
            "total_ask_size": "10",
            "orderbook_units": [{"bid_price": "99", "ask_price": "100"}],
        },
        {
            "market": "KRW-ETH",
            "total_bid_size": "7",
            "total_ask_size": "10",
            "orderbook_units": [{"bid_price": "49", "ask_price": "50"}],
        },
    ]
    coingecko = Mock()
    coingecko.get_markets.return_value = [
        {
            "id": "ethereum",
            "symbol": "eth",
            "market_cap_rank": 2,
            "current_price": 3000,
            "market_cap": 100,
            "total_volume": 10,
            "price_change_percentage_1h_in_currency": 1.1,
            "price_change_percentage_24h_in_currency": 2.2,
            "price_change_percentage_7d_in_currency": 3.3,
            "price_change_percentage_30d_in_currency": 4.4,
            "last_updated": "now",
        },
        {"id": "bitcoin", "symbol": "btc", "market_cap_rank": 1},
    ]
    fear = Mock()
    fear.get_latest.return_value = {"available": True, "value": 50}
    return upbit, coingecko, fear


def service(
    upbit: Mock, coingecko: Mock, fear: Mock, config: Settings | None = None
) -> MarketDataContextService:
    return MarketDataContextService(upbit, coingecko, fear, config or settings())


def test_enriches_all_sources_once_in_bulk_without_mutating_input() -> None:
    upbit, coingecko, fear = clients()
    candidates = [
        {"market": "KRW-BTC", "coingecko_id": "bitcoin"},
        {"market": "KRW-ETH", "coingecko_id": "ethereum"},
    ]
    original = deepcopy(candidates)
    result = service(upbit, coingecko, fear).enrich_candidates(candidates)

    upbit.get_orderbooks.assert_called_once_with(["KRW-BTC", "KRW-ETH"], count=15)
    coingecko.get_markets.assert_called_once_with(["bitcoin", "ethereum"])
    fear.get_latest.assert_called_once_with()
    assert candidates == original
    assert result.external_data_status == {
        "upbit_orderbook": "AVAILABLE",
        "coingecko": "AVAILABLE",
        "fear_greed": "AVAILABLE",
    }
    assert result.candidates[0]["orderbook"]["spread"] == "1"
    assert result.candidates[0]["orderbook"]["spread_rate"] == "0.01"
    assert result.candidates[0]["orderbook"]["pressure_label"] == "BUY_PRESSURE"
    assert result.candidates[1]["orderbook"]["pressure_label"] == "SELL_PRESSURE"
    assert result.candidates[0]["global_market"]["id"] == "bitcoin"
    assert result.candidates[1]["global_market"]["current_price_usd"] == "3000"
    assert result.market_sentiment == {"available": True, "value": 50}


@pytest.mark.parametrize("failed", ["upbit", "coingecko", "fear"])
def test_each_source_failure_is_independent_and_unavailable(failed: str) -> None:
    upbit, coingecko, fear = clients()
    target = {
        "upbit": upbit.get_orderbooks,
        "coingecko": coingecko.get_markets,
        "fear": fear.get_latest,
    }[failed]
    target.side_effect = RuntimeError("secret-free failure")
    result = service(upbit, coingecko, fear).enrich_candidates(
        [{"market": "KRW-BTC", "coingecko_id": "bitcoin"}]
    )
    status_key = {
        "upbit": "upbit_orderbook",
        "coingecko": "coingecko",
        "fear": "fear_greed",
    }[failed]
    assert result.external_data_status[status_key] == "UNAVAILABLE"
    assert (
        sum(value == "AVAILABLE" for value in result.external_data_status.values()) == 2
    )


def test_disabled_sources_are_not_called() -> None:
    upbit, coingecko, fear = clients()
    result = service(
        upbit,
        coingecko,
        fear,
        settings(
            upbit_orderbook_enabled=False,
            coingecko_enabled=False,
            fear_greed_enabled=False,
        ),
    ).enrich_candidates([{"market": "KRW-BTC"}])
    upbit.get_orderbooks.assert_not_called()
    coingecko.get_markets.assert_not_called()
    fear.get_latest.assert_not_called()
    assert set(result.external_data_status.values()) == {"DISABLED"}
    assert result.candidates[0]["orderbook"]["reason"] == "disabled"
    assert result.candidates[0]["global_market"]["reason"] == "disabled"
    assert result.market_sentiment["reason"] == "disabled"


def test_missing_rows_and_ids_are_handled_per_candidate() -> None:
    upbit, coingecko, fear = clients()
    upbit.get_orderbooks.return_value = []
    coingecko.get_markets.return_value = []
    result = service(upbit, coingecko, fear).enrich_candidates(
        [
            {"market": "KRW-XRP", "coingecko_id": "ripple"},
            {"market": "KRW-UNKNOWN"},
        ]
    )
    assert result.candidates[0]["orderbook"]["reason"] == "market_not_returned"
    assert result.candidates[0]["global_market"]["reason"] == "coin_not_returned"
    assert result.candidates[1]["global_market"]["reason"] == "missing_coingecko_id"


def test_empty_candidates_skip_all_external_calls() -> None:
    upbit, coingecko, fear = clients()
    result = service(upbit, coingecko, fear).enrich_candidates([])
    upbit.get_orderbooks.assert_not_called()
    coingecko.get_markets.assert_not_called()
    fear.get_latest.assert_not_called()
    assert result.candidates == []
    assert result.market_sentiment == {"available": False, "reason": "no_candidates"}
    assert set(result.external_data_status.values()) == {"DISABLED"}
