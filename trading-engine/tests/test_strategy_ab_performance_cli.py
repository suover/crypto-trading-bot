from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from scripts.evaluate_strategy_ab_performance import (
    batch_report,
    parse_arguments,
    run,
    snapshot_report,
)


def snapshot_result(**changes):
    values = {
        "snapshot_id": 2,
        "pipeline_run_id": "pipeline-2",
        "captured_at": "2026-09-01T10:00:00+00:00",
        "horizon_minutes": 240,
        "baseline_policy_signature": "strategy-replay-v1:base",
        "scenario_signature": "offline-replay-v1:scenario",
        "replay_status": "SUCCESS",
        "replay_safe_reason": None,
        "status": "SUCCESS",
        "safe_reason": None,
        "performance_evaluated": True,
        "effective_top_n": 2,
        "baseline_top_markets": ("KRW-A", "KRW-B"),
        "scenario_top_markets": ("KRW-A", "KRW-C"),
        "top_n_overlap_count": 1,
        "top_n_overlap_rate": Decimal("0.5"),
        "entered_top_n": ("KRW-C",),
        "exited_top_n": ("KRW-B",),
        "baseline_complete_count": 2,
        "baseline_required_count": 2,
        "baseline_missing_markets": (),
        "scenario_complete_count": 2,
        "scenario_required_count": 2,
        "scenario_missing_markets": (),
        "baseline_candidate_count": 2,
        "scenario_candidate_count": 2,
        "baseline_mean_return": Decimal("1"),
        "scenario_mean_return": Decimal("2"),
        "mean_return_delta": Decimal("1"),
        "baseline_median_return": Decimal("1"),
        "scenario_median_return": Decimal("2"),
        "median_return_delta": Decimal("1"),
        "baseline_positive_count": 1,
        "scenario_positive_count": 2,
        "baseline_negative_count": 1,
        "scenario_negative_count": 0,
        "baseline_flat_count": 0,
        "scenario_flat_count": 0,
        "baseline_positive_rate": Decimal("0.5"),
        "scenario_positive_rate": Decimal("1"),
        "positive_rate_delta": Decimal("0.5"),
        "scenario_result": "SCENARIO_WIN",
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "args",
    [
        ["--snapshot-id", "1", "--latest", "2", "--horizon", "60"],
        ["--snapshot-id", "0", "--horizon", "60"],
        ["--latest", "0", "--horizon", "60"],
        ["--snapshot-id", "1", "--horizon", "0"],
        ["--snapshot-id", "1"],
        ["--snapshot-id", "1", "--horizon", "60", "--override", "unknown=1"],
        ["--snapshot-id", "1", "--horizon", "60", "--override", "bad"],
        [
            "--snapshot-id",
            "1",
            "--horizon",
            "60",
            "--override",
            "liquidity=0.2",
            "--override",
            "liquidity=0.3",
        ],
    ],
)
def test_parser_rejects_invalid_selection_horizon_and_overrides(args) -> None:
    with pytest.raises(SystemExit) as error:
        parse_arguments(args)
    assert error.value.code == 2


def test_single_cli_forwards_existing_override_semantics_and_reports_success(
    monkeypatch,
) -> None:
    namespace = parse_arguments(
        [
            "--snapshot-id",
            "2",
            "--horizon",
            "240",
            "--override",
            "liquidity=0.20",
            "--override",
            "momentum=0.30",
        ]
    )
    service = MagicMock()
    service.evaluate_snapshot.return_value = snapshot_result()
    monkeypatch.setattr(
        "scripts.evaluate_strategy_ab_performance.StrategyABPerformanceService",
        lambda _: service,
    )
    lines, exit_code = run(MagicMock(), namespace)
    service.evaluate_snapshot.assert_called_once_with(
        2,
        horizon_minutes=240,
        overrides={"liquidity": Decimal("0.20"), "momentum": Decimal("0.30")},
    )
    assert exit_code == 0
    assert "result_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE" in lines
    assert "performance_evaluated=true" in lines
    assert "outcome_coverage=PASS" in lines
    assert "scenario_result=SCENARIO_WIN" in lines


def test_single_incomplete_report_has_no_partial_metrics_and_nonzero_exit(
    monkeypatch,
) -> None:
    namespace = parse_arguments(["--snapshot-id", "2", "--horizon", "1440"])
    service = MagicMock()
    service.evaluate_snapshot.return_value = snapshot_result(
        horizon_minutes=1440,
        status="OUTCOME_INCOMPLETE",
        safe_reason="selected TopN outcome coverage is incomplete",
        performance_evaluated=False,
        baseline_complete_count=1,
        baseline_missing_markets=("KRW-B",),
        baseline_mean_return=None,
        scenario_mean_return=None,
        mean_return_delta=None,
        scenario_result=None,
    )
    monkeypatch.setattr(
        "scripts.evaluate_strategy_ab_performance.StrategyABPerformanceService",
        lambda _: service,
    )
    lines, exit_code = run(MagicMock(), namespace)
    assert exit_code == 1
    assert "status=OUTCOME_INCOMPLETE" in lines
    assert "performance_evaluated=false" in lines
    assert "baseline_missing_markets=KRW-B" in lines
    assert "baseline_mean_return=None" in lines


def test_batch_cli_reports_descriptive_aggregate_and_mixed_statuses(
    monkeypatch,
) -> None:
    namespace = parse_arguments(["--latest", "100", "--horizon", "60"])
    service = MagicMock()
    service.evaluate_latest.return_value = SimpleNamespace(
        requested_snapshot_count=100,
        evaluated_snapshot_count=2,
        successful_snapshot_count=1,
        outcome_incomplete_count=1,
        baseline_integrity_failed_count=0,
        replay_incompatible_count=0,
        invalid_outcome_count=0,
        scenario_win_count=1,
        scenario_loss_count=0,
        tie_count=0,
        scenario_win_rate=Decimal("1"),
        mean_baseline_return=Decimal("1"),
        mean_scenario_return=Decimal("2"),
        mean_return_delta=Decimal("1"),
        median_snapshot_return_delta=Decimal("1"),
        mean_baseline_positive_rate=Decimal("0.5"),
        mean_scenario_positive_rate=Decimal("1"),
        results=(
            snapshot_result(),
            snapshot_result(
                snapshot_id=3,
                status="OUTCOME_INCOMPLETE",
                performance_evaluated=False,
                mean_return_delta=None,
                scenario_result=None,
            ),
        ),
    )
    monkeypatch.setattr(
        "scripts.evaluate_strategy_ab_performance.StrategyABPerformanceService",
        lambda _: service,
    )
    lines, exit_code = run(MagicMock(), namespace)
    assert exit_code == 0
    assert "report_type=STRATEGY_AB_PERFORMANCE_BATCH" in lines
    assert "successful_snapshot_count=1" in lines
    assert "outcome_incomplete_count=1" in lines
    assert any("snapshot_id=3 status=OUTCOME_INCOMPLETE" in line for line in lines)


def test_baseline_integrity_failure_is_explicit_in_report() -> None:
    lines = snapshot_report(
        snapshot_result(
            status="BASELINE_INTEGRITY_FAILED",
            performance_evaluated=False,
            replay_status="BASELINE_MISMATCH",
            replay_safe_reason="stored ranking was not reproduced",
            scenario_result=None,
        )
    )
    assert "baseline_integrity=FAIL" in lines
    assert "performance_evaluated=false" in lines


def test_batch_report_uses_required_result_type() -> None:
    result = MagicMock()
    result.requested_snapshot_count = result.evaluated_snapshot_count = 0
    result.successful_snapshot_count = result.outcome_incomplete_count = 0
    result.baseline_integrity_failed_count = result.replay_incompatible_count = 0
    result.invalid_outcome_count = result.scenario_win_count = 0
    result.scenario_loss_count = result.tie_count = 0
    result.scenario_win_rate = None
    result.mean_baseline_return = result.mean_scenario_return = None
    result.mean_return_delta = result.median_snapshot_return_delta = None
    result.mean_baseline_positive_rate = result.mean_scenario_positive_rate = None
    result.results = ()
    assert "result_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE" in batch_report(
        result
    )


def test_existing_weight_validation_rejects_invalid_sum_during_evaluation() -> None:
    namespace = parse_arguments(
        ["--snapshot-id", "2", "--horizon", "60", "--override", "momentum=0.30"]
    )
    service = MagicMock()
    service.evaluate_snapshot.side_effect = ReplayInputError(
        "ranking component weights must sum to 1"
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "scripts.evaluate_strategy_ab_performance.StrategyABPerformanceService",
            lambda _: service,
        )
        with pytest.raises(ReplayInputError, match="must sum to 1"):
            run(MagicMock(), namespace)
