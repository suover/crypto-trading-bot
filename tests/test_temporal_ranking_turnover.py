from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import (
    BatchReplayResult,
    SnapshotReplayResult,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    INSUFFICIENT_TEMPORAL_TRANSITIONS,
    INVALID_TURNOVER_DATA,
    NO_COMMON_REPLAYABLE_SNAPSHOTS,
    NO_REPLAY_SNAPSHOTS,
    SUCCESS,
    TemporalRankingTurnoverService,
)
from scripts import evaluate_temporal_ranking_turnover as cli


WEIGHTS = {
    "liquidity": "0.35",
    "trend_alignment": "0.20",
    "momentum": "0.15",
    "volume_confirmation": "0.10",
    "spread": "0.08",
    "volatility": "0.07",
    "drawdown": "0.05",
}


def _definitions(count: int = 2):
    scenarios = []
    for index in range(count):
        weights = dict(WEIGHTS)
        if index:
            weights["liquidity"] = "0.30"
            weights["momentum"] = "0.20"
        scenarios.append(
            {"name": f"scenario_{index + 1}", "component_weights": weights}
        )
    return parse_scenario_document(
        {"schema_version": "ranking-scenario-sweep-v1", "scenarios": scenarios}
    )


def _replay(
    snapshot_id: int,
    baseline: tuple[str, ...],
    scenario: tuple[str, ...] | None = None,
    *,
    captured_at: datetime | None = None,
    status: str = SUCCESS,
    matches: bool = True,
    policy: str = "policy-a",
    scenario_signature: str = "scenario-signature",
) -> SnapshotReplayResult:
    return SnapshotReplayResult(
        snapshot_id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        captured_at=captured_at
        if captured_at is not None
        else datetime(2026, 1, snapshot_id, tzinfo=UTC),
        dataset_schema_version="strategy-replay-v1",
        baseline_policy_signature=policy,
        scenario_signature=scenario_signature if status == SUCCESS else None,
        status=status,
        safe_reason=None if status == SUCCESS else "invalid replay",
        rankable_candidate_count=len(baseline),
        stored_top_n=len(baseline),
        requested_top_n=len(baseline),
        effective_top_n=len(baseline) if status == SUCCESS else 0,
        held_augmented_count=0,
        baseline_matches_stored=matches,
        baseline_top_markets=baseline if status == SUCCESS else (),
        scenario_top_markets=(scenario or baseline) if status == SUCCESS else (),
        top_n_overlap_count=0,
        top_n_overlap_rate=Decimal("0"),
        entered_top_n=(),
        exited_top_n=(),
        candidate_results=(),
        mismatch_diagnostics=(),
    )


def _batch(results: tuple[SnapshotReplayResult, ...], requested: int = 10):
    return BatchReplayResult(
        requested_snapshot_count=requested,
        replayed_snapshot_count=len(results),
        compatible_snapshot_count=0,
        incompatible_snapshot_count=0,
        baseline_match_count=0,
        baseline_mismatch_count=0,
        mean_top_n_overlap_rate=Decimal("0"),
        mean_absolute_rank_change=Decimal("0"),
        total_entered_top_n=0,
        total_exited_top_n=0,
        results=results,
    )


def _evaluate(scenario_batches: tuple[BatchReplayResult, ...], *, latest: int = 10):
    fake = MagicMock()
    fake.replay_latest.side_effect = scenario_batches
    return TemporalRankingTurnoverService(MagicMock(), replay_service=fake).evaluate(
        scenarios=_definitions(len(scenario_batches)), latest=latest
    )


def test_top_seven_one_replacement_uses_entered_count_as_proxy() -> None:
    previous = _replay(1, tuple(f"M{i}" for i in range(7)))
    current = _replay(2, (*tuple(f"M{i}" for i in range(6)), "NEW"))

    transition = TemporalRankingTurnoverService._transition(
        previous, current, top_n=7, use_baseline=True
    )

    assert transition.retained_count == 6
    assert transition.entered_markets == ("NEW",)
    assert transition.exited_markets == ("M6",)
    assert transition.replacement_rate == Decimal(1) / Decimal(7)
    assert transition.retention_rate == Decimal(6) / Decimal(7)
    assert transition.retention_rate + transition.replacement_rate == 1


@pytest.mark.parametrize(
    ("current", "replacement", "retention"),
    [
        (("A", "B"), Decimal("0"), Decimal("1")),
        (("C", "D"), Decimal("1"), Decimal("0")),
    ],
)
def test_zero_and_full_replacement(current, replacement, retention) -> None:
    transition = TemporalRankingTurnoverService._transition(
        _replay(1, ("A", "B")),
        _replay(2, current),
        top_n=2,
        use_baseline=True,
    )
    assert transition.replacement_rate == replacement
    assert transition.retention_rate == retention


def test_temporal_baseline_scenarios_deltas_ordering_and_summary() -> None:
    baseline_rows = (
        _replay(3, ("B", "C"), ("B", "X")),
        _replay(2, ("A", "B"), ("A", "B")),
        _replay(1, ("A", "B"), ("A", "B")),
    )
    second_rows = tuple(
        replace(row, scenario_top_markets=("A", "B"), scenario_signature="sig-2")
        for row in baseline_rows
    )

    result = _evaluate((_batch(baseline_rows), _batch(second_rows)))

    assert result.status == SUCCESS
    cohort = result.cohorts[0]
    assert [item.baseline.previous_snapshot_id for item in cohort.transitions] == [1, 2]
    assert cohort.transitions[1].baseline.retained_markets == ("B",)
    assert cohort.transitions[1].baseline.entered_markets == ("C",)
    assert cohort.transitions[1].baseline.exited_markets == ("A",)
    first_scenario, second_scenario = cohort.transitions[1].scenarios
    assert first_scenario.replacement_rate_delta_vs_baseline == 0
    assert second_scenario.replacement_rate_delta_vs_baseline == Decimal("-0.5")
    assert cohort.baseline_summary.mean_replacement_rate == Decimal("0.25")
    assert cohort.baseline_summary.median_replacement_rate == Decimal("0.25")
    assert cohort.baseline_summary.min_replacement_rate == 0
    assert cohort.baseline_summary.max_replacement_rate == Decimal("0.5")
    assert cohort.baseline_summary.total_entered_count == 1
    assert cohort.baseline_summary.total_exited_count == 1
    assert cohort.baseline_summary.zero_replacement_transition_count == 1
    assert cohort.baseline_summary.full_replacement_transition_count == 0
    assert cohort.scenario_summaries[
        1
    ].mean_replacement_rate_delta_vs_baseline == Decimal("-0.25")


def test_scenario_more_replacement_has_positive_delta_and_full_count() -> None:
    rows = (
        _replay(2, ("A", "B"), ("C", "D")),
        _replay(1, ("A", "B"), ("A", "B")),
    )
    result = _evaluate((_batch(rows),))
    transition = result.cohorts[0].transitions[0]
    assert transition.baseline.replacement_rate == 0
    assert transition.scenarios[0].replacement_rate_delta_vs_baseline == 1
    assert (
        result.cohorts[0]
        .scenario_summaries[0]
        .summary.full_replacement_transition_count
        == 1
    )


def test_chronology_uses_snapshot_id_for_equal_timestamps() -> None:
    same_time = datetime(2026, 1, 1, tzinfo=UTC)
    rows = (
        _replay(2, ("B",), captured_at=same_time),
        _replay(1, ("A",), captured_at=same_time),
    )
    result = _evaluate((_batch(rows),), latest=10)
    transition = result.cohorts[0].transitions[0].baseline
    assert (transition.previous_snapshot_id, transition.current_snapshot_id) == (1, 2)


def test_invalid_middle_snapshot_is_not_removed_and_bridged() -> None:
    good = (
        _replay(3, ("C",)),
        _replay(2, (), status="INVALID_REPLAY_DATA", matches=False),
        _replay(1, ("A",)),
    )
    other = tuple(
        replace(row, scenario_signature="sig-2" if row.status == SUCCESS else None)
        for row in good
    )

    result = _evaluate((_batch(good), _batch(other)))

    assert result.status == INSUFFICIENT_TEMPORAL_TRANSITIONS
    assert result.cohorts[0].transitions == ()
    assert result.cohorts[0].continuity_break_count == 2


def test_one_scenario_failure_breaks_all_scenario_continuity() -> None:
    rows = tuple(_replay(i, (f"M{i}",)) for i in (3, 2, 1))
    other = (
        replace(rows[0], scenario_signature="sig-2"),
        replace(
            rows[1],
            status="INVALID_REPLAY_DATA",
            baseline_matches_stored=False,
            effective_top_n=0,
            baseline_top_markets=(),
            scenario_top_markets=(),
            scenario_signature=None,
        ),
        replace(rows[2], scenario_signature="sig-2"),
    )
    result = _evaluate((_batch(rows), _batch(other)))
    assert result.status == INSUFFICIENT_TEMPORAL_TRANSITIONS
    assert result.cohorts[0].transition_count == 0


def test_policy_and_top_n_boundaries_create_separate_nonbridged_cohorts() -> None:
    rows = (
        _replay(4, ("D",), policy="policy-b"),
        _replay(3, ("C",), policy="policy-b"),
        _replay(2, ("A", "B"), policy="policy-a"),
        _replay(1, ("A", "B"), policy="policy-a"),
    )
    result = _evaluate((_batch(rows),))
    assert result.status == SUCCESS
    assert result.cohort_count == 2
    assert sorted(cohort.transition_count for cohort in result.cohorts) == [1, 1]
    assert all(
        transition.baseline.previous_snapshot_id
        in ({1} if cohort.effective_top_n == 2 else {3})
        for cohort in result.cohorts
        for transition in cohort.transitions
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: (rows[0], replace(rows[1], snapshot_id=rows[0].snapshot_id)),
        lambda rows: (
            rows[0],
            replace(rows[1], captured_at=rows[1].captured_at.replace(tzinfo=None)),
        ),
    ],
)
def test_duplicate_id_and_naive_timestamp_fail_closed(mutate) -> None:
    rows = (_replay(2, ("B",)), _replay(1, ("A",)))
    result = _evaluate((_batch(mutate(rows)),))
    assert result.status == INVALID_TURNOVER_DATA


def test_scenario_snapshot_set_and_common_baseline_metadata_mismatch_fail_closed() -> (
    None
):
    rows = (_replay(2, ("B",)), _replay(1, ("A",)))
    missing = _evaluate((_batch(rows), _batch(rows[:1])))
    assert missing.status == INVALID_TURNOVER_DATA

    other = tuple(
        replace(row, baseline_top_markets=("X",), scenario_signature="sig-2")
        for row in rows
    )
    mismatch = _evaluate((_batch(rows), _batch(other)))
    assert mismatch.status == INVALID_TURNOVER_DATA

    identity_mismatch = (
        replace(rows[0], pipeline_run_id="different", scenario_signature="sig-2"),
        replace(rows[1], scenario_signature="sig-2"),
    )
    invalid_identity = _evaluate((_batch(rows), _batch(identity_mismatch)))
    assert invalid_identity.status == INVALID_TURNOVER_DATA


@pytest.mark.parametrize(
    "current",
    [("A",), ("A", "A")],
)
def test_top_n_count_and_duplicate_market_fail_closed(current) -> None:
    current_result = _replay(2, current)
    if len(current) == 1:
        current_result = replace(current_result, effective_top_n=2)
    rows = (current_result, _replay(1, ("A", "B")))
    result = _evaluate((_batch(rows),))
    assert result.status == INVALID_TURNOVER_DATA


def test_safe_empty_single_and_no_common_states() -> None:
    empty = _evaluate((_batch((), requested=10),))
    assert empty.status == NO_REPLAY_SNAPSHOTS

    single = _evaluate((_batch((_replay(1, ("A",)),)),))
    assert single.status == INSUFFICIENT_TEMPORAL_TRANSITIONS

    invalid = _replay(1, (), status="INVALID_REPLAY_DATA", matches=False)
    no_common = _evaluate((_batch((invalid,)),))
    assert no_common.status == NO_COMMON_REPLAYABLE_SNAPSHOTS


def test_cli_has_no_horizon_and_reports_research_safety_flags() -> None:
    with pytest.raises(SystemExit):
        cli.parse_arguments(
            ["--latest", "2", "--scenario-file", "x", "--horizon", "60"]
        )
    with pytest.raises(SystemExit):
        cli.parse_arguments(["--latest", "0", "--scenario-file", "x"])

    result = _evaluate(
        (
            _batch(
                (_replay(2, ("B",)), _replay(1, ("A",))),
            ),
        )
    )
    output = "\n".join(cli.report(result))
    assert "candidate_outcome_data_used=false" in output
    assert "horizon_dependent=false" in output
    assert "monetary_turnover_computed=false" in output
    assert "rebalance_notional_computed=false" in output
    assert "transaction_cost_computed=false" in output
    assert "winner" not in output.lower()
    assert "promotion" not in output.lower()


def test_cli_exit_codes_for_success_safe_and_invalid_results(monkeypatch) -> None:
    namespace = SimpleNamespace(latest=10, scenario_file="unused")
    monkeypatch.setattr(cli, "load_scenario_file", lambda _path: _definitions(1))
    service = MagicMock()
    monkeypatch.setattr(
        "crypto_trading_bot.services.temporal_ranking_turnover_service."
        "TemporalRankingTurnoverService",
        lambda _session: service,
    )
    for status, expected in (
        (SUCCESS, 0),
        (INSUFFICIENT_TEMPORAL_TRANSITIONS, 0),
        (INVALID_TURNOVER_DATA, 1),
    ):
        service.evaluate.return_value = replace(
            _evaluate((_batch((_replay(1, ("A",)),)),)), status=status
        )
        _lines, exit_code = cli.run(MagicMock(), namespace)
        assert exit_code == expected
