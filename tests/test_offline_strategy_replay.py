from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    HeuristicRankingWeights,
)
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.offline_strategy_replay_service import (
    OfflineStrategyReplayService,
    ReplayInputError,
    apply_overrides,
    parse_override,
    restore_weights,
    scenario_signature,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


def _feature(
    market: str,
    liquidity: str,
    momentum: str,
    *,
    enough_candles: bool = True,
) -> dict:
    return {
        "market": market,
        "quote_trade_value_24h": liquidity,
        "timeframes": {
            "15m": {
                "data_quality": "SUFFICIENT",
                "trend_label": "관망",
                "recent_change_rate": momentum,
                "volume_ratio": "100",
                "realized_volatility": "10",
                "max_drawdown": "15",
            }
        },
        "orderbook": {"spread_rate": "0.005"},
        "enough_candles": enough_candles,
    }


def _policy_data(
    weights: HeuristicRankingWeights | None = None, *, top_n: int = 2
) -> dict:
    settings = Settings(
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=top_n,
        market_universe_prefilter_n=10,
    )
    return build_policy_data(
        settings, HeuristicMarketRankingPolicy(weights or HeuristicRankingWeights())
    )


def _snapshot(
    policy_data: dict | None = None,
    *,
    snapshot_id: int = 1,
    schema: str = DATASET_SCHEMA_VERSION,
) -> SimpleNamespace:
    stored_policy = policy_data if policy_data is not None else _policy_data()
    return SimpleNamespace(
        id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        captured_at=datetime(2026, 1, snapshot_id, tzinfo=UTC),
        dataset_schema_version=schema,
        policy_signature=policy_signature(stored_policy),
        policy_data=stored_policy,
        research_candidate_count=3,
        prefilter_candidate_count=3,
        ranked_candidate_count=3,
        final_candidate_count=2,
    )


def _ranked_rows(
    weights: HeuristicRankingWeights | None = None,
) -> tuple[list[SimpleNamespace], list[dict]]:
    features = [
        _feature("KRW-BTC", "1000", "-10"),
        _feature("KRW-ETH", "800", "0"),
        _feature("KRW-XRP", "600", "10"),
    ]
    ranked = HeuristicMarketRankingPolicy(weights).rank(features)
    ranking = {
        candidate["market"]: (
            rank,
            candidate["score"].quantize(Decimal("0.000000001")),
        )
        for rank, candidate in enumerate(ranked, start=1)
    }
    rows = []
    for prefilter_rank, feature in enumerate(features, start=1):
        original_rank, original_score = ranking[feature["market"]]
        rows.append(
            SimpleNamespace(
                strategy_replay_snapshot_id=1,
                market=feature["market"],
                in_prefilter=True,
                prefilter_rank=prefilter_rank,
                buy_eligible=True,
                held=False,
                original_rank=original_rank,
                original_score=original_score,
                final_selected=original_rank <= 2,
                selection_source="RANKED" if original_rank <= 2 else None,
                feature_data=feature,
            )
        )
    return rows, features


def _replay(
    *,
    snapshot: SimpleNamespace | None = None,
    rows: list[SimpleNamespace] | None = None,
    overrides: dict[str, Decimal] | None = None,
    top_n: int | None = None,
):
    default_rows, _ = _ranked_rows()
    replay_rows = rows or default_rows
    replay_snapshot = snapshot or _snapshot()
    replay_snapshot.research_candidate_count = len(replay_rows)
    replay_snapshot.prefilter_candidate_count = sum(
        row.in_prefilter for row in replay_rows
    )
    replay_snapshot.ranked_candidate_count = sum(
        row.original_rank is not None for row in replay_rows
    )
    replay_snapshot.final_candidate_count = sum(
        row.final_selected for row in replay_rows
    )
    return OfflineStrategyReplayService(MagicMock())._replay_loaded(
        replay_snapshot,
        replay_rows,
        overrides=overrides,
        top_n=top_n,
    )


def test_stored_policy_reconstructs_every_decimal_weight_without_defaults() -> None:
    custom = replace(
        HeuristicRankingWeights(),
        liquidity=Decimal("0.30"),
        momentum=Decimal("0.20"),
        momentum_reference_percent=Decimal("7.125"),
    )
    restored, top_n = restore_weights(_policy_data(custom, top_n=7))
    assert restored == custom
    assert all(isinstance(value, Decimal) for value in asdict(restored).values())
    assert top_n == 7


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        (
            lambda data: data["ranking"].update(policy_name="OtherPolicy"),
            "UNSUPPORTED_RANKING_POLICY",
        ),
        (
            lambda data: data["ranking"]["weights"].pop("liquidity"),
            "INVALID_REPLAY_DATA",
        ),
        (
            lambda data: data["ranking"]["weights"].update(liquidity="NaN"),
            "INVALID_REPLAY_DATA",
        ),
    ],
)
def test_unknown_or_invalid_stored_policy_fails_closed(
    mutation, expected_status
) -> None:
    data = _policy_data()
    mutation(data)
    result = _replay(snapshot=_snapshot(data))
    assert result.status == expected_status
    assert result.candidate_results == ()


def test_unknown_dataset_schema_fails_closed_without_current_settings_fallback() -> (
    None
):
    result = _replay(snapshot=_snapshot(schema="strategy-replay-dataset-v2"))
    assert result.status == "UNSUPPORTED_DATASET_SCHEMA"
    assert result.scenario_signature is None


def test_rankable_pool_excludes_non_prefilter_ineligible_insufficient_and_held() -> (
    None
):
    rows, _ = _ranked_rows()
    rows.extend(
        [
            SimpleNamespace(
                strategy_replay_snapshot_id=1,
                market="KRW-DOGE",
                in_prefilter=False,
                prefilter_rank=None,
                buy_eligible=True,
                held=True,
                original_rank=None,
                original_score=None,
                final_selected=True,
                selection_source="HELD",
                feature_data=_feature("KRW-DOGE", "50", "10"),
            ),
            SimpleNamespace(
                strategy_replay_snapshot_id=1,
                market="KRW-ADA",
                in_prefilter=True,
                prefilter_rank=4,
                buy_eligible=False,
                held=False,
                original_rank=None,
                original_score=None,
                final_selected=False,
                selection_source=None,
                feature_data=_feature("KRW-ADA", "500", "10"),
            ),
            SimpleNamespace(
                strategy_replay_snapshot_id=1,
                market="KRW-SOL",
                in_prefilter=True,
                prefilter_rank=5,
                buy_eligible=True,
                held=False,
                original_rank=None,
                original_score=None,
                final_selected=False,
                selection_source=None,
                feature_data=_feature("KRW-SOL", "400", "10", enough_candles=False),
            ),
        ]
    )
    result = _replay(rows=rows)
    assert result.status == "SUCCESS"
    assert result.rankable_candidate_count == 3
    assert result.held_augmented_count == 1
    assert {item.market for item in result.candidate_results} == {
        "KRW-BTC",
        "KRW-ETH",
        "KRW-XRP",
    }
    assert "KRW-DOGE" not in result.baseline_top_markets


@pytest.mark.parametrize(
    "corrupt",
    [
        "missing_enough_candles",
        "duplicate_prefilter_rank",
        "missing_original_rank",
        "non_rankable_has_rank",
        "market_mismatch",
    ],
)
def test_corrupt_candidate_data_is_invalid(corrupt: str) -> None:
    rows, _ = _ranked_rows()
    if corrupt == "missing_enough_candles":
        rows[0].feature_data.pop("enough_candles")
    elif corrupt == "duplicate_prefilter_rank":
        rows[1].prefilter_rank = rows[0].prefilter_rank
    elif corrupt == "missing_original_rank":
        rows[0].original_rank = None
    elif corrupt == "non_rankable_has_rank":
        rows[0].buy_eligible = False
    else:
        rows[0].feature_data["market"] = "KRW-NOT-BTC"
    result = _replay(rows=rows)
    assert result.status == "INVALID_REPLAY_DATA"


def test_baseline_reproduces_stored_ranks_and_numeric_scores() -> None:
    result = _replay()
    assert result.status == "SUCCESS"
    assert result.baseline_matches_stored is True
    assert all(
        candidate.original_rank == candidate.baseline_replay_rank
        for candidate in result.candidate_results
    )
    assert all(
        abs(candidate.baseline_replay_score - candidate.original_score)
        <= Decimal("0.0000000005")
        for candidate in result.candidate_results
    )


def test_tiny_stored_score_rounding_is_within_integrity_tolerance() -> None:
    rows, _ = _ranked_rows()
    rows[0].original_score += Decimal("0.0000000004")
    assert _replay(rows=rows).status == "SUCCESS"


def test_rank_or_material_score_mismatch_returns_diagnostics() -> None:
    rows, _ = _ranked_rows()
    first, second = sorted(rows, key=lambda row: row.original_rank)[:2]
    first.original_rank, second.original_rank = (
        second.original_rank,
        first.original_rank,
    )
    first.original_score += Decimal("0.000001")
    result = _replay(rows=rows)
    assert result.status == "BASELINE_MISMATCH"
    assert result.baseline_matches_stored is False
    assert {item.market for item in result.mismatch_diagnostics} >= {first.market}
    diagnostic = next(
        item for item in result.mismatch_diagnostics if item.market == first.market
    )
    assert diagnostic.stored_original_rank != diagnostic.replay_baseline_rank
    assert diagnostic.score_delta != 0


def test_non_finite_stored_score_is_invalid_replay_data() -> None:
    rows, _ = _ranked_rows()
    rows[0].original_score = Decimal("Infinity")
    assert _replay(rows=rows).status == "INVALID_REPLAY_DATA"


def test_snapshot_candidate_count_mismatch_is_invalid_replay_data() -> None:
    rows, _ = _ranked_rows()
    snapshot = _snapshot()
    result = OfflineStrategyReplayService(MagicMock())._replay_loaded(
        snapshot, rows, overrides=None, top_n=None
    )
    snapshot.research_candidate_count += 1
    corrupt = OfflineStrategyReplayService(MagicMock())._replay_loaded(
        snapshot, rows, overrides=None, top_n=None
    )
    assert result.status == "SUCCESS"
    assert corrupt.status == "INVALID_REPLAY_DATA"


def test_alternative_weights_rerank_and_compute_top_n_changes() -> None:
    overrides = {
        "liquidity": Decimal("0.10"),
        "momentum": Decimal("0.40"),
    }
    result = _replay(overrides=overrides, top_n=1)
    assert result.status == "SUCCESS"
    assert result.requested_top_n == 1
    assert result.effective_top_n == 1
    assert result.baseline_top_markets != result.scenario_top_markets
    assert result.top_n_overlap_count == 0
    assert result.top_n_overlap_rate == 0
    assert len(result.entered_top_n) == 1
    assert len(result.exited_top_n) == 2
    changed = next(item for item in result.candidate_results if item.entered_top_n)
    assert (
        changed.rank_delta_vs_original == changed.scenario_rank - changed.original_rank
    )
    assert (
        changed.score_delta_vs_original
        == changed.scenario_score - changed.original_score
    )


def test_top_n_above_pool_uses_effective_rankable_count() -> None:
    result = _replay(top_n=100)
    assert result.requested_top_n == 100
    assert result.effective_top_n == 3
    assert len(result.scenario_top_markets) == 3


def test_final_selected_held_is_not_original_ranked_top_n() -> None:
    rows, _ = _ranked_rows()
    held = SimpleNamespace(
        strategy_replay_snapshot_id=1,
        market="KRW-DOGE",
        in_prefilter=False,
        prefilter_rank=None,
        buy_eligible=True,
        held=True,
        original_rank=None,
        original_score=None,
        final_selected=True,
        selection_source="HELD",
        feature_data=_feature("KRW-DOGE", "50", "10"),
    )
    result = _replay(rows=[*rows, held])
    assert result.held_augmented_count == 1
    assert "KRW-DOGE" not in result.baseline_top_markets
    assert "KRW-DOGE" not in result.scenario_top_markets


def test_reference_override_uses_stored_baseline_and_is_supported() -> None:
    baseline = replace(
        HeuristicRankingWeights(), momentum_reference_percent=Decimal("8")
    )
    scenario = apply_overrides(
        baseline, {"momentum_reference_percent": Decimal("12.25")}
    )
    assert scenario.momentum_reference_percent == Decimal("12.25")
    assert scenario.liquidity == baseline.liquidity


@pytest.mark.parametrize(
    "override",
    [
        {"unknown": Decimal("1")},
        {"liquidity": Decimal("-0.01"), "momentum": Decimal("0.51")},
        {"momentum": Decimal("0.30")},
        {"spread_reference_rate": Decimal("0")},
        {"spread_reference_rate": Decimal("Infinity")},
    ],
)
def test_invalid_scenario_overrides_are_rejected(override) -> None:
    with pytest.raises(ReplayInputError):
        apply_overrides(HeuristicRankingWeights(), override)


@pytest.mark.parametrize(
    "value", ["unknown=1", "liquidity=NaN", "liquidity=Infinity", "liquidity"]
)
def test_invalid_override_text_is_rejected(value: str) -> None:
    with pytest.raises(ReplayInputError):
        parse_override(value)


def test_scenario_signature_is_canonical_and_sensitive() -> None:
    weights = HeuristicRankingWeights()
    assert scenario_signature(weights, 2) == scenario_signature(
        HeuristicRankingWeights(**dict(reversed(list(asdict(weights).items())))), 2
    )
    assert scenario_signature(weights, 2) != scenario_signature(
        replace(weights, liquidity=Decimal("0.34"), momentum=Decimal("0.16")), 2
    )
    assert scenario_signature(weights, 2) != scenario_signature(weights, 3)
    assert scenario_signature(
        replace(weights, liquidity=Decimal("0.350")), 2
    ) == scenario_signature(replace(weights, liquidity=Decimal("0.35")), 2)


def test_replay_snapshot_not_found_is_safe_and_read_only() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    result = OfflineStrategyReplayService(session).replay_snapshot(999)
    assert result.status == "INVALID_REPLAY_DATA"
    assert result.safe_reason == "snapshot not found"
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_loaded_replay_has_no_session_writes_or_external_dependencies() -> None:
    session = MagicMock()
    rows, _ = _ranked_rows()
    result = OfflineStrategyReplayService(session)._replay_loaded(
        _snapshot(), rows, overrides=None, top_n=None
    )
    assert result.status == "SUCCESS"
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    assert not hasattr(OfflineStrategyReplayService(session), "provider")
    assert not hasattr(OfflineStrategyReplayService(session), "client")


def test_batch_summary_keeps_compatible_results_after_invalid_snapshot() -> None:
    valid = _replay()
    invalid = _replay(snapshot=_snapshot(schema="unknown", snapshot_id=2))
    service = OfflineStrategyReplayService(MagicMock())
    summary = service.summarize_results(2, [valid, invalid])
    assert summary.replayed_snapshot_count == 2
    assert summary.compatible_snapshot_count == 1
    assert summary.incompatible_snapshot_count == 1
    assert summary.baseline_match_count == 1
    assert summary.baseline_mismatch_count == 0
    assert summary.mean_top_n_overlap_rate == valid.top_n_overlap_rate


def test_batch_snapshots_restore_their_own_historical_policy() -> None:
    weights_a = HeuristicRankingWeights()
    weights_b = replace(weights_a, liquidity=Decimal("0.25"), momentum=Decimal("0.25"))
    rows_a, _ = _ranked_rows(weights_a)
    rows_b, _ = _ranked_rows(weights_b)
    result_a = _replay(
        snapshot=_snapshot(_policy_data(weights_a), snapshot_id=1), rows=rows_a
    )
    result_b = _replay(
        snapshot=_snapshot(_policy_data(weights_b), snapshot_id=2), rows=rows_b
    )
    assert result_a.status == result_b.status == "SUCCESS"
    assert result_a.baseline_policy_signature != result_b.baseline_policy_signature
    assert result_a.scenario_signature != result_b.scenario_signature
