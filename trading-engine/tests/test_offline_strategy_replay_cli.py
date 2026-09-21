from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import (
    BatchReplayResult,
    SnapshotReplayResult,
)
from scripts.replay_strategy_rankings import (
    batch_report,
    parse_arguments,
    run,
    snapshot_report,
)


def _result(**overrides) -> SnapshotReplayResult:
    values = {
        "snapshot_id": 1,
        "pipeline_run_id": "pipeline-1",
        "captured_at": None,
        "dataset_schema_version": "strategy-replay-dataset-v1",
        "baseline_policy_signature": "strategy-replay-v1:base",
        "scenario_signature": "offline-replay-v1:scenario",
        "status": "SUCCESS",
        "safe_reason": None,
        "rankable_candidate_count": 2,
        "stored_top_n": 1,
        "requested_top_n": 1,
        "effective_top_n": 1,
        "held_augmented_count": 1,
        "baseline_matches_stored": True,
        "baseline_top_markets": ("KRW-BTC",),
        "scenario_top_markets": ("KRW-ETH",),
        "top_n_overlap_count": 0,
        "top_n_overlap_rate": Decimal("0"),
        "entered_top_n": ("KRW-ETH",),
        "exited_top_n": ("KRW-BTC",),
        "candidate_results": (),
        "mismatch_diagnostics": (),
    }
    values.update(overrides)
    return SnapshotReplayResult(**values)


def test_cli_defaults_to_latest_one() -> None:
    namespace = parse_arguments([])
    service = MagicMock()
    service.replay_latest.return_value = BatchReplayResult(
        requested_snapshot_count=1,
        replayed_snapshot_count=0,
        compatible_snapshot_count=0,
        incompatible_snapshot_count=0,
        baseline_match_count=0,
        baseline_mismatch_count=0,
        mean_top_n_overlap_rate=Decimal("0"),
        mean_absolute_rank_change=Decimal("0"),
        total_entered_top_n=0,
        total_exited_top_n=0,
        results=(),
    )
    session = MagicMock()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "scripts.replay_strategy_rankings.OfflineStrategyReplayService",
            lambda _: service,
        )
        lines, exit_code = run(session, namespace)
    service.replay_latest.assert_called_once_with(1, overrides={}, top_n=None)
    assert exit_code == 0
    assert "performance_evaluation=false" in lines


def test_cli_snapshot_override_and_top_n_are_forwarded() -> None:
    namespace = parse_arguments(
        [
            "--snapshot-id",
            "7",
            "--override",
            "liquidity=0.20",
            "--override",
            "momentum=0.30",
            "--top-n",
            "5",
        ]
    )
    service = MagicMock()
    service.replay_snapshot.return_value = _result(snapshot_id=7)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "scripts.replay_strategy_rankings.OfflineStrategyReplayService",
            lambda _: service,
        )
        lines, exit_code = run(MagicMock(), namespace)
    service.replay_snapshot.assert_called_once_with(
        7,
        overrides={"liquidity": Decimal("0.20"), "momentum": Decimal("0.30")},
        top_n=5,
    )
    assert exit_code == 0
    assert lines[0] == "report_type=OFFLINE_STRATEGY_REPLAY"


@pytest.mark.parametrize(
    "args",
    [
        ["--snapshot-id", "1", "--latest", "2"],
        ["--snapshot-id", "0"],
        ["--latest", "0"],
        ["--top-n", "0"],
        ["--override", "unknown=1"],
        ["--override", "liquidity=NaN"],
        ["--override", "liquidity=0.2", "--override", "liquidity=0.3"],
    ],
)
def test_cli_rejects_invalid_selection_and_overrides(args) -> None:
    with pytest.raises(SystemExit) as error:
        parse_arguments(args)
    assert error.value.code == 2


def test_single_snapshot_failure_has_nonzero_exit_and_safe_diagnostic() -> None:
    namespace = parse_arguments(["--snapshot-id", "999"])
    service = MagicMock()
    service.replay_snapshot.return_value = _result(
        snapshot_id=999,
        status="INVALID_REPLAY_DATA",
        safe_reason="snapshot not found",
        baseline_matches_stored=False,
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "scripts.replay_strategy_rankings.OfflineStrategyReplayService",
            lambda _: service,
        )
        lines, exit_code = run(MagicMock(), namespace)
    assert exit_code == 1
    assert "status=INVALID_REPLAY_DATA" in lines
    assert "safe_reason=snapshot not found" in lines


def test_batch_report_labels_results_as_ranking_counterfactual_only() -> None:
    result = BatchReplayResult(
        requested_snapshot_count=2,
        replayed_snapshot_count=2,
        compatible_snapshot_count=1,
        incompatible_snapshot_count=1,
        baseline_match_count=1,
        baseline_mismatch_count=0,
        mean_top_n_overlap_rate=Decimal("0.5"),
        mean_absolute_rank_change=Decimal("1"),
        total_entered_top_n=1,
        total_exited_top_n=1,
        results=(_result(), _result(snapshot_id=2, status="INVALID_REPLAY_DATA")),
    )
    lines = batch_report(result)
    assert "result_type=RANKING_COUNTERFACTUAL_ONLY" in lines
    assert "performance_evaluation=false" in lines
    assert "incompatible_snapshot_count=1" in lines


def test_snapshot_report_separates_held_augmentation_from_ranked_top_n() -> None:
    lines = snapshot_report(_result())
    assert "held_augmented_count=1" in lines
    assert "baseline_top_markets=KRW-BTC" in lines
    assert all("profit" not in line.lower() for line in lines)
