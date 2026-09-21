from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    HeuristicRankingWeights,
)
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    StrategyReplayDatasetService,
    build_policy_data,
    policy_signature,
)


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://test:test@localhost/test",
        "market_universe_mode": "DYNAMIC",
        "market_universe_top_n": 2,
        "market_universe_prefilter_n": 4,
    }
    values.update(overrides)
    return Settings(**values)


def replay_candidate(market: str, liquidity: str, *, held: bool = False) -> dict:
    quote, base = market.split("-")
    return {
        "exchange": "UPBIT",
        "market": market,
        "base_asset": base,
        "quote_asset": quote,
        "latest_price": "100",
        "liquidity": {"quote_trade_value_24h": liquidity},
        "quote_trade_value_24h": liquidity,
        "market_event": {"warning": False, "caution": False},
        "timeframes": {"15m": {"data_quality": "SUFFICIENT"}},
        "orderbook": {"available": True, "spread_rate": "0.001"},
        "data_quality": "SUFFICIENT",
        "enough_candles": True,
        "held": held,
        "buy_eligible": True,
        "sell_eligible": held,
        "trading_supported": True,
    }


def test_policy_signature_is_canonical_and_sensitive_to_policy_inputs() -> None:
    base_settings = settings(market_blocklist="KRW-XRP,KRW-ADA")
    reordered = settings(market_blocklist="KRW-ADA,KRW-XRP")
    policy = HeuristicMarketRankingPolicy()
    base_data = build_policy_data(base_settings, policy)
    assert policy_signature(base_data) == policy_signature(
        {key: base_data[key] for key in reversed(base_data)}
    )
    assert policy_signature(base_data) == policy_signature(
        build_policy_data(reordered, policy)
    )
    changed_weights = replace(HeuristicRankingWeights(), liquidity=Decimal("0.36"))
    assert policy_signature(base_data) != policy_signature(
        build_policy_data(base_settings, HeuristicMarketRankingPolicy(changed_weights))
    )
    assert policy_signature(base_data) != policy_signature(
        build_policy_data(settings(market_universe_top_n=3), policy)
    )


def test_capture_preserves_prefilter_full_ranking_final_and_held_semantics() -> None:
    session = MagicMock()

    def assign_snapshot_id(row) -> None:
        if isinstance(row, StrategyReplaySnapshot):
            row.id = 77

    session.add.side_effect = assign_snapshot_id
    btc = replay_candidate("KRW-BTC", "400")
    eth = replay_candidate("KRW-ETH", "300")
    xrp = replay_candidate("KRW-XRP", "200")
    held = replay_candidate("KRW-DOGE", "10", held=True)
    ranked = [
        {**btc, "score": Decimal("0.9")},
        {**eth, "score": Decimal("0.8")},
        {**xrp, "score": Decimal("0.7")},
    ]
    final = [
        {**ranked[0], "rank": 1, "selection_source": "RANKED"},
        {**ranked[1], "rank": 2, "selection_source": "RANKED"},
        {
            **held,
            "rank": None,
            "score": None,
            "selection_source": "HELD",
            "buy_eligible": False,
        },
    ]
    result = StrategyReplayDatasetService(session).capture(
        analysis_run=SimpleNamespace(id=12, pipeline_run_id="pipeline-1"),
        user=SimpleNamespace(id=5),
        exchange="UPBIT",
        quote_asset="KRW",
        settings=settings(),
        ranking_policy=HeuristicMarketRankingPolicy(),
        research_candidates=[btc, eth, xrp, held],
        liquidity_prefilter=[btc, eth, xrp],
        ranked_candidates=ranked,
        final_candidates=final,
    )
    assert result.snapshot.dataset_schema_version == DATASET_SCHEMA_VERSION
    assert result.snapshot.research_candidate_count == 4
    assert result.snapshot.prefilter_candidate_count == 3
    assert result.snapshot.ranked_candidate_count == 3
    assert result.snapshot.final_candidate_count == 3
    rows = {row.market: row for row in result.candidates}
    assert rows["KRW-BTC"].prefilter_rank == 1
    assert rows["KRW-XRP"].original_rank == 3
    assert rows["KRW-XRP"].original_score == Decimal("0.7")
    assert rows["KRW-XRP"].final_selected is False
    assert rows["KRW-DOGE"].prefilter_rank is None
    assert rows["KRW-DOGE"].original_rank is None
    assert rows["KRW-DOGE"].final_selected is True
    assert rows["KRW-DOGE"].selection_source == "HELD"
    assert rows["KRW-DOGE"].buy_eligible is True
    assert rows["KRW-DOGE"].feature_data["buy_eligible"] is True
    assert "timeframes" in rows["KRW-XRP"].feature_data
