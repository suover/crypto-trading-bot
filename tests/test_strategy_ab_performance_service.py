from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import (
    SnapshotReplayResult,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    INVALID_OUTCOME_DATA,
    OUTCOME_INCOMPLETE,
    REPLAY_INCOMPATIBLE,
    SUCCESS,
    StrategyABPerformanceService,
)


def replay_result(**changes) -> SnapshotReplayResult:
    values = {
        "snapshot_id": 7,
        "pipeline_run_id": "pipeline-7",
        "captured_at": datetime(2026, 9, 1, 10, 13, tzinfo=UTC),
        "dataset_schema_version": "strategy-replay-dataset-v1",
        "baseline_policy_signature": "strategy-replay-v1:base",
        "scenario_signature": "offline-replay-v1:scenario",
        "status": "SUCCESS",
        "safe_reason": None,
        "rankable_candidate_count": 3,
        "stored_top_n": 2,
        "requested_top_n": 2,
        "effective_top_n": 2,
        "held_augmented_count": 0,
        "baseline_matches_stored": True,
        "baseline_top_markets": ("KRW-A", "KRW-B"),
        "scenario_top_markets": ("KRW-A", "KRW-C"),
        "top_n_overlap_count": 1,
        "top_n_overlap_rate": Decimal("0.5"),
        "entered_top_n": ("KRW-C",),
        "exited_top_n": ("KRW-B",),
        "candidate_results": (),
        "mismatch_diagnostics": (),
    }
    values.update(changes)
    return SnapshotReplayResult(**values)


def outcome_entry(
    market: str,
    value: object,
    *,
    horizon: int = 60,
    status: str = "COMPLETE",
    candidate_id: int | None = None,
    snapshot_id: int = 7,
    outcome_snapshot_id: int | None = None,
):
    identifier = candidate_id or ord(market[-1])
    candidate = SimpleNamespace(
        id=identifier,
        strategy_replay_snapshot_id=snapshot_id,
        analysis_run_id=11,
        user_id=3,
        exchange="UPBIT",
        market=market,
    )
    outcome = SimpleNamespace(
        strategy_replay_candidate_id=identifier,
        strategy_replay_snapshot_id=(
            snapshot_id if outcome_snapshot_id is None else outcome_snapshot_id
        ),
        user_id=3,
        exchange="UPBIT",
        market=market,
        horizon_minutes=horizon,
        evaluation_status=status,
        market_return_percentage=value,
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        analysis_run_id=11,
        user_id=3,
        exchange="UPBIT",
    )
    return candidate, outcome, snapshot


def outcome_map(values: dict[str, object], *, horizon: int = 60, snapshot_id: int = 7):
    return {
        (snapshot_id, market): [
            outcome_entry(market, value, horizon=horizon, snapshot_id=snapshot_id)
        ]
        for market, value in values.items()
    }


def evaluate(replay=None, outcomes=None, *, horizon=60):
    return StrategyABPerformanceService(MagicMock())._evaluate_replay(
        replay or replay_result(), horizon, outcomes or {}
    )


def test_equal_weight_metrics_overlap_and_scenario_win_are_decimal_exact() -> None:
    result = evaluate(
        outcomes=outcome_map(
            {"KRW-A": Decimal("10"), "KRW-B": Decimal("-10"), "KRW-C": "20"}
        )
    )
    assert result.status == SUCCESS
    assert result.performance_evaluated is True
    assert result.baseline_mean_return == 0
    assert result.scenario_mean_return == 15
    assert result.mean_return_delta == 15
    assert result.baseline_median_return == 0
    assert result.scenario_median_return == 15
    assert result.median_return_delta == 15
    assert (result.baseline_positive_count, result.baseline_negative_count) == (1, 1)
    assert result.baseline_flat_count == 0
    assert result.scenario_positive_count == 2
    assert result.scenario_negative_count == result.scenario_flat_count == 0
    assert result.baseline_positive_rate == Decimal("0.5")
    assert result.scenario_positive_rate == 1
    assert result.positive_rate_delta == Decimal("0.5")
    assert result.scenario_result == "SCENARIO_WIN"
    assert result.top_n_overlap_count == 1
    assert result.entered_top_n == ("KRW-C",)
    assert result.exited_top_n == ("KRW-B",)


def test_scenario_loss_and_tie_use_exact_decimal_comparison() -> None:
    loss = evaluate(
        outcomes=outcome_map({"KRW-A": "10", "KRW-B": "-10", "KRW-C": "-20"})
    )
    assert loss.mean_return_delta == Decimal("-5")
    assert loss.scenario_result == "SCENARIO_LOSS"

    identical = replay_result(
        scenario_top_markets=("KRW-A", "KRW-B"),
        top_n_overlap_count=2,
        top_n_overlap_rate=Decimal("1"),
        entered_top_n=(),
        exited_top_n=(),
    )
    tie = evaluate(
        replay=identical,
        outcomes=outcome_map({"KRW-A": "1", "KRW-B": "0"}),
    )
    assert tie.baseline_mean_return == tie.scenario_mean_return
    assert tie.mean_return_delta == tie.median_return_delta == 0
    assert tie.positive_rate_delta == 0
    assert tie.scenario_result == "TIE"
    assert tie.top_n_overlap_rate == 1


def test_top_seven_fixture_can_lose_at_one_horizon_and_win_at_another() -> None:
    baseline = tuple(f"KRW-{market}" for market in "ABCDEFG")
    scenario = tuple(f"KRW-{market}" for market in "ADFHIBJ")
    replay = replay_result(
        rankable_candidate_count=10,
        stored_top_n=7,
        requested_top_n=7,
        effective_top_n=7,
        baseline_top_markets=baseline,
        scenario_top_markets=scenario,
        top_n_overlap_count=4,
        top_n_overlap_rate=Decimal(4) / Decimal(7),
        entered_top_n=("KRW-H", "KRW-I", "KRW-J"),
        exited_top_n=("KRW-C", "KRW-E", "KRW-G"),
    )
    one_hour_returns = {
        market: ("10" if market in {"KRW-C", "KRW-E", "KRW-G"} else "0")
        for market in set(baseline) | set(scenario)
    }
    one_hour_returns.update({market: "-10" for market in ("KRW-H", "KRW-I", "KRW-J")})
    four_hour_returns = {
        market: ("-10" if market in {"KRW-C", "KRW-E", "KRW-G"} else "0")
        for market in set(baseline) | set(scenario)
    }
    four_hour_returns.update({market: "10" for market in ("KRW-H", "KRW-I", "KRW-J")})

    one_hour = evaluate(
        replay=replay,
        outcomes=outcome_map(one_hour_returns, horizon=60),
        horizon=60,
    )
    four_hour = evaluate(
        replay=replay,
        outcomes=outcome_map(four_hour_returns, horizon=240),
        horizon=240,
    )
    assert one_hour.scenario_result == "SCENARIO_LOSS"
    assert (
        one_hour.mean_return_delta
        == one_hour.scenario_mean_return - one_hour.baseline_mean_return
    )
    assert four_hour.scenario_result == "SCENARIO_WIN"
    assert (
        four_hour.mean_return_delta
        == four_hour.scenario_mean_return - four_hour.baseline_mean_return
    )
    assert one_hour.top_n_overlap_count == four_hour.top_n_overlap_count == 4


def test_missing_or_partial_outcome_forbids_all_partial_metrics() -> None:
    outcomes = outcome_map({"KRW-A": "10", "KRW-C": "20"})
    outcomes[(7, "KRW-B")] = [outcome_entry("KRW-B", None, status="PARTIAL")]
    result = evaluate(outcomes=outcomes)
    assert result.status == OUTCOME_INCOMPLETE
    assert result.performance_evaluated is False
    assert result.baseline_complete_count == 1
    assert result.baseline_required_count == 2
    assert result.scenario_complete_count == 2
    assert result.baseline_missing_markets == ("KRW-B",)
    assert result.baseline_mean_return is None
    assert result.scenario_mean_return is None
    assert result.mean_return_delta is None


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "-Infinity", "bad"])
def test_complete_outcome_with_invalid_return_fails_closed(value) -> None:
    result = evaluate(
        outcomes=outcome_map({"KRW-A": "10", "KRW-B": value, "KRW-C": "20"})
    )
    assert result.status == INVALID_OUTCOME_DATA
    assert result.performance_evaluated is False
    assert result.baseline_mean_return is None


def test_duplicate_or_lineage_mismatch_fails_closed() -> None:
    duplicate = outcome_map({"KRW-A": "10", "KRW-B": "0", "KRW-C": "20"})
    duplicate[(7, "KRW-A")].append(outcome_entry("KRW-A", "10"))
    assert evaluate(outcomes=duplicate).status == INVALID_OUTCOME_DATA

    mismatch = outcome_map({"KRW-A": "10", "KRW-B": "0", "KRW-C": "20"})
    mismatch[(7, "KRW-C")] = [outcome_entry("KRW-C", "20", outcome_snapshot_id=99)]
    assert evaluate(outcomes=mismatch).status == INVALID_OUTCOME_DATA


def test_baseline_mismatch_and_incompatible_replay_never_query_or_evaluate() -> None:
    for replay, expected in (
        (
            replay_result(
                status="BASELINE_MISMATCH",
                safe_reason="stored ranking was not reproduced",
                baseline_matches_stored=False,
            ),
            BASELINE_INTEGRITY_FAILED,
        ),
        (
            replay_result(
                status="UNSUPPORTED_DATASET_SCHEMA",
                safe_reason="unsupported dataset",
                baseline_matches_stored=False,
                baseline_top_markets=(),
                scenario_top_markets=(),
                effective_top_n=0,
            ),
            REPLAY_INCOMPATIBLE,
        ),
        (
            replay_result(
                status="INVALID_REPLAY_DATA",
                safe_reason="snapshot not found",
                baseline_matches_stored=False,
                baseline_top_markets=(),
                scenario_top_markets=(),
                effective_top_n=0,
            ),
            REPLAY_INCOMPATIBLE,
        ),
    ):
        result = evaluate(replay=replay)
        assert result.status == expected
        assert result.performance_evaluated is False


def test_arbitrary_explicit_horizon_uses_only_matching_stored_rows() -> None:
    complete = evaluate(
        outcomes=outcome_map({"KRW-A": "1", "KRW-B": "2", "KRW-C": "3"}, horizon=30),
        horizon=30,
    )
    assert complete.status == SUCCESS
    assert complete.horizon_minutes == 30
    wrong_horizon = evaluate(
        outcomes=outcome_map({"KRW-A": "1", "KRW-B": "2", "KRW-C": "3"}, horizon=60),
        horizon=30,
    )
    assert wrong_horizon.status == INVALID_OUTCOME_DATA


def test_batch_aggregates_success_only_and_handles_zero_success() -> None:
    win = evaluate(outcomes=outcome_map({"KRW-A": "10", "KRW-B": "-10", "KRW-C": "20"}))
    loss = evaluate(
        replay=replay_result(snapshot_id=8, pipeline_run_id="pipeline-8"),
        outcomes=outcome_map(
            {"KRW-A": "10", "KRW-B": "-10", "KRW-C": "-20"},
            snapshot_id=8,
        ),
    )
    incomplete = replace(win, status=OUTCOME_INCOMPLETE, performance_evaluated=False)
    summary = StrategyABPerformanceService._summarize(3, (win, loss, incomplete))
    assert summary.successful_snapshot_count == 2
    assert summary.outcome_incomplete_count == 1
    assert (summary.scenario_win_count, summary.scenario_loss_count) == (1, 1)
    assert summary.tie_count == 0
    assert summary.scenario_win_rate == Decimal("0.5")
    assert summary.mean_return_delta == Decimal("5")
    assert summary.median_snapshot_return_delta == Decimal("5")

    empty = StrategyABPerformanceService._summarize(1, (incomplete,))
    assert empty.successful_snapshot_count == 0
    assert empty.scenario_win_rate is None
    assert empty.mean_return_delta is None
    assert empty.median_snapshot_return_delta is None


def test_batch_reports_each_excluded_status_without_adding_it_to_aggregates() -> None:
    success = evaluate(
        outcomes=outcome_map({"KRW-A": "10", "KRW-B": "-10", "KRW-C": "20"})
    )
    results = (
        success,
        replace(success, status=OUTCOME_INCOMPLETE, performance_evaluated=False),
        replace(
            success,
            status=BASELINE_INTEGRITY_FAILED,
            performance_evaluated=False,
        ),
        replace(success, status=REPLAY_INCOMPATIBLE, performance_evaluated=False),
        replace(success, status=INVALID_OUTCOME_DATA, performance_evaluated=False),
    )
    summary = StrategyABPerformanceService._summarize(5, results)
    assert summary.successful_snapshot_count == 1
    assert summary.outcome_incomplete_count == 1
    assert summary.baseline_integrity_failed_count == 1
    assert summary.replay_incompatible_count == 1
    assert summary.invalid_outcome_count == 1
    assert summary.mean_return_delta == success.mean_return_delta


def test_public_summary_helper_preserves_existing_batch_semantics() -> None:
    success = evaluate(
        outcomes=outcome_map({"KRW-A": "10", "KRW-B": "-10", "KRW-C": "20"})
    )
    service = StrategyABPerformanceService(MagicMock())
    assert service.summarize_results(1, (success,)) == service._summarize(1, (success,))
    for invalid in (-1, True, Decimal("1")):
        with pytest.raises(Exception, match="requested snapshot count must be >= 0"):
            service.summarize_results(invalid, ())


def test_evaluate_snapshot_reuses_replay_and_performs_one_read_query() -> None:
    replay = replay_result()
    replay_service = MagicMock()
    replay_service.replay_snapshot.return_value = replay
    session = MagicMock()
    session.execute.return_value = [
        outcome_entry("KRW-A", "10"),
        outcome_entry("KRW-B", "-10"),
        outcome_entry("KRW-C", "20"),
    ]
    result = StrategyABPerformanceService(
        session, replay_service=replay_service
    ).evaluate_snapshot(7, horizon_minutes=60, overrides={})
    assert result.status == SUCCESS
    replay_service.replay_snapshot.assert_called_once_with(7, overrides={}, top_n=None)
    session.execute.assert_called_once()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5])
def test_invalid_horizon_is_rejected(horizon) -> None:
    with pytest.raises(Exception, match="horizon must be a positive integer"):
        StrategyABPerformanceService(MagicMock()).evaluate_snapshot(
            1, horizon_minutes=horizon
        )


def test_service_has_no_external_provider_or_write_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "crypto_trading_bot"
        / "services"
        / "strategy_ab_performance_service.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "UpbitMarketDataProvider",
        "UpbitClient",
        "OpenAI",
        "Telegram",
        "CoinGecko",
        "self.session.add(",
        "self.session.flush(",
        "self.session.commit(",
    ):
        assert forbidden not in source
