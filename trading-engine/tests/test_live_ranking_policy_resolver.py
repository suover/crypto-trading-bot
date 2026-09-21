from copy import deepcopy
from unittest.mock import MagicMock

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    BASELINE,
    LiveRankingPolicyResolver,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    build_policy_data,
    policy_signature,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
    )


def _candidates() -> list[dict[str, object]]:
    return [
        {
            "market": "KRW-BTC",
            "quote_trade_value_24h": "2000000000",
            "timeframes": {},
            "orderbook": {"spread_rate": "0.001"},
        },
        {
            "market": "KRW-ETH",
            "quote_trade_value_24h": "1000000000",
            "timeframes": {},
            "orderbook": {"spread_rate": "0.002"},
        },
    ]


def test_inspect_returns_canonical_baseline_without_database_activity() -> None:
    session = MagicMock()
    settings = _settings()
    resolver = LiveRankingPolicyResolver(session, settings=settings)

    resolution = resolver.inspect(user_id=1, exchange="UPBIT", quote_asset="KRW")

    expected_policy = HeuristicMarketRankingPolicy()
    assert resolution.user_id == 1
    assert resolution.mode == BASELINE
    assert isinstance(resolution.ranking_policy, HeuristicMarketRankingPolicy)
    assert resolution.baseline_policy_signature == policy_signature(
        build_policy_data(settings, expected_policy)
    )
    session.scalar.assert_not_called()
    session.scalars.assert_called_once()
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_resolved_policy_matches_existing_baseline_ranking() -> None:
    resolver = LiveRankingPolicyResolver(MagicMock(), settings=_settings())
    candidates = _candidates()

    actual = resolver.inspect(
        user_id=1, exchange="UPBIT", quote_asset="KRW"
    ).ranking_policy.rank(deepcopy(candidates))
    expected = HeuristicMarketRankingPolicy().rank(deepcopy(candidates))

    assert actual == expected


def test_resolve_lease_is_baseline_only_and_has_no_run_reservation() -> None:
    session = MagicMock()
    resolver = LiveRankingPolicyResolver(session, settings=_settings())

    with resolver.resolve(user_id=1, exchange="UPBIT", quote_asset="KRW") as lease:
        assert lease.resolution.mode == BASELINE
        assert isinstance(lease.ranking_policy, HeuristicMarketRankingPolicy)
        assert not hasattr(lease, "reserve_run")

    session.scalar.assert_not_called()
    session.scalars.assert_called_once()
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
