from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    INVALID_COST_ADJUSTED_DATA,
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    NO_COST_ADJUSTABLE_SNAPSHOTS,
    NO_TURNOVER_TRANSITIONS,
    SUCCESS,
    CostAdjustedRankingEvaluationService,
    build_cost_assumptions,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS as SWEEP_NO_COMMON,
    SUCCESS as SWEEP_SUCCESS,
    RankingScenarioComparableCohort,
    RankingScenarioEvaluationMatrix,
    parse_scenario_document,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    OUTCOME_INCOMPLETE,
    SUCCESS as AB_SUCCESS,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    INSUFFICIENT_TEMPORAL_TRANSITIONS,
    INVALID_TURNOVER_DATA,
    RankingSelectionTransition,
    TemporalRankingTurnoverScenarioTransition,
    TemporalRankingTurnoverTransition,
)
from scripts import evaluate_cost_adjusted_ranking as cli


WEIGHTS = {
    "liquidity": "0.35",
    "trend_alignment": "0.20",
    "momentum": "0.15",
    "volume_confirmation": "0.10",
    "spread": "0.08",
    "volatility": "0.07",
    "drawdown": "0.05",
}


def _definitions(count: int = 1):
    scenarios = []
    for index in range(count):
        weights = dict(WEIGHTS)
        if index:
            weights["liquidity"] = "0.30"
            weights["momentum"] = "0.20"
        scenarios.append({"name": f"scenario_{index}", "component_weights": weights})
    return parse_scenario_document(
        {"schema_version": "ranking-scenario-sweep-v1", "scenarios": scenarios}
    )


def _selection_transition(
    *,
    top_n: int,
    replaced: int,
    current_id: int = 2,
    prefix: str = "B",
) -> RankingSelectionTransition:
    retained_count = top_n - replaced
    previous = tuple(f"{prefix}-R{i}" for i in range(retained_count)) + tuple(
        f"{prefix}-X{i}" for i in range(replaced)
    )
    current = tuple(f"{prefix}-R{i}" for i in range(retained_count)) + tuple(
        f"{prefix}-E{i}" for i in range(replaced)
    )
    retained = previous[:retained_count]
    exited = previous[retained_count:]
    entered = current[retained_count:]
    return RankingSelectionTransition(
        previous_snapshot_id=current_id - 1,
        current_snapshot_id=current_id,
        previous_captured_at=datetime(2026, 1, current_id - 1, tzinfo=UTC),
        current_captured_at=datetime(2026, 1, current_id, tzinfo=UTC),
        effective_top_n=top_n,
        previous_top_markets=previous,
        current_top_markets=current,
        retained_markets=retained,
        entered_markets=entered,
        exited_markets=exited,
        retained_count=retained_count,
        entered_count=replaced,
        exited_count=replaced,
        retention_rate=Decimal(retained_count) / Decimal(top_n),
        replacement_rate=Decimal(replaced) / Decimal(top_n),
    )


def _ab_result(
    baseline: RankingSelectionTransition,
    scenario: RankingSelectionTransition,
    *,
    horizon: int = 60,
    status: str = AB_SUCCESS,
    scenario_signature: str = "scenario-signature",
    baseline_return: Decimal = Decimal("1.50"),
    scenario_return: Decimal = Decimal("2.00"),
) -> StrategyABSnapshotPerformanceResult:
    successful = status == AB_SUCCESS
    return StrategyABSnapshotPerformanceResult(
        snapshot_id=baseline.current_snapshot_id,
        pipeline_run_id=f"pipeline-{baseline.current_snapshot_id}",
        captured_at=baseline.current_captured_at,
        horizon_minutes=horizon,
        baseline_policy_signature="policy-a",
        scenario_signature=scenario_signature,
        replay_status="SUCCESS",
        replay_safe_reason=None,
        status=status,
        safe_reason=None if successful else "outcome incomplete",
        performance_evaluated=successful,
        effective_top_n=baseline.effective_top_n,
        baseline_top_markets=baseline.current_top_markets,
        scenario_top_markets=scenario.current_top_markets,
        top_n_overlap_count=0,
        top_n_overlap_rate=Decimal("0"),
        entered_top_n=(),
        exited_top_n=(),
        baseline_required_count=baseline.effective_top_n,
        scenario_required_count=baseline.effective_top_n,
        baseline_complete_count=baseline.effective_top_n if successful else 0,
        scenario_complete_count=baseline.effective_top_n if successful else 0,
        baseline_missing_markets=(),
        scenario_missing_markets=(),
        baseline_candidate_count=baseline.effective_top_n if successful else 0,
        scenario_candidate_count=baseline.effective_top_n if successful else 0,
        baseline_mean_return=baseline_return if successful else None,
        scenario_mean_return=scenario_return if successful else None,
        mean_return_delta=(scenario_return - baseline_return) if successful else None,
        baseline_median_return=baseline_return if successful else None,
        scenario_median_return=scenario_return if successful else None,
        median_return_delta=(scenario_return - baseline_return) if successful else None,
        baseline_positive_count=1 if successful else None,
        scenario_positive_count=1 if successful else None,
        baseline_negative_count=0 if successful else None,
        scenario_negative_count=0 if successful else None,
        baseline_flat_count=0 if successful else None,
        scenario_flat_count=0 if successful else None,
        baseline_positive_rate=Decimal("1") if successful else None,
        scenario_positive_rate=Decimal("1") if successful else None,
        positive_rate_delta=Decimal("0") if successful else None,
        scenario_result="SCENARIO_WIN" if successful else None,
    )


def _inputs(
    *,
    baseline_replaced: int = 1,
    scenario_replaced: int = 2,
    current_id: int = 2,
    horizon: int = 60,
    ab_status: str = AB_SUCCESS,
    common_ids: tuple[int, ...] | None = None,
    turnover_status: str = SUCCESS,
):
    definitions = _definitions()
    baseline = _selection_transition(
        top_n=2, replaced=baseline_replaced, current_id=current_id, prefix="B"
    )
    scenario_selection = _selection_transition(
        top_n=2, replaced=scenario_replaced, current_id=current_id, prefix="S"
    )
    scenario = TemporalRankingTurnoverScenarioTransition(
        scenario_name=definitions[0].name,
        scenario_signature="scenario-signature",
        transition=scenario_selection,
        replacement_rate_delta_vs_baseline=(
            scenario_selection.replacement_rate - baseline.replacement_rate
        ),
    )
    temporal = TemporalRankingTurnoverTransition(
        transition_index=1, baseline=baseline, scenarios=(scenario,)
    )
    scenario_summary = SimpleNamespace(
        scenario_name=definitions[0].name,
        scenario_definition_signature=definitions[0].definition_signature,
        scenario_signature="scenario-signature",
    )
    turnover_cohort = SimpleNamespace(
        baseline_policy_signature="policy-a",
        effective_top_n=2,
        transitions=(temporal,),
        transition_count=1,
        scenario_summaries=(scenario_summary,),
    )
    turnover = SimpleNamespace(
        requested_snapshot_count=5,
        replayed_snapshot_count=5,
        status=turnover_status,
        safe_reason=None,
        scenarios=definitions,
        cohorts=(turnover_cohort,) if turnover_status == SUCCESS else (),
    )
    row = _ab_result(baseline, scenario_selection, horizon=horizon, status=ab_status)
    resolved_common = (
        common_ids
        if common_ids is not None
        else ((row.snapshot_id,) if ab_status == AB_SUCCESS else ())
    )
    ab_rows = [row]
    for snapshot_id in resolved_common:
        if snapshot_id != row.snapshot_id:
            ab_rows.append(
                replace(
                    row,
                    snapshot_id=snapshot_id,
                    pipeline_run_id=f"pipeline-{snapshot_id}",
                    captured_at=datetime(2026, 1, snapshot_id, tzinfo=UTC),
                )
            )
    candidate_ids = tuple(sorted({1, row.snapshot_id, *resolved_common}))
    ab_cohort = RankingScenarioComparableCohort(
        horizon_minutes=horizon,
        baseline_policy_signature="policy-a",
        effective_top_n=2,
        candidate_snapshot_ids=candidate_ids,
        common_snapshot_ids=resolved_common,
        common_coverage_rate=Decimal(len(resolved_common))
        / Decimal(len(candidate_ids)),
        status=SWEEP_SUCCESS if resolved_common else SWEEP_NO_COMMON,
        safe_reason=None if resolved_common else "no common",
        scenario_results=((definitions[0].name, tuple(ab_rows)),),
    )
    matrix = RankingScenarioEvaluationMatrix(
        requested_snapshot_count=5,
        evaluated_snapshot_count=2,
        scenarios=definitions,
        horizons=(horizon,),
        cohorts=(ab_cohort,),
    )
    return definitions, turnover, matrix, baseline, scenario, row


def _evaluate(turnover, matrix, definitions):
    turnover_service = MagicMock()
    turnover_service.evaluate.return_value = turnover
    performance = StrategyABPerformanceService(MagicMock())
    sweep = MagicMock()
    sweep.performance_service = performance
    sweep.evaluate_matrix.return_value = matrix
    return CostAdjustedRankingEvaluationService(
        MagicMock(),
        turnover_service=turnover_service,
        sweep_service=sweep,
        performance_service=performance,
    ).evaluate(
        scenarios=definitions,
        horizons=matrix.horizons,
        latest=5,
        fee_rate="0.0005",
        spread_cost_rate="0.0005",
        slippage_rate="0.001",
    )


@pytest.mark.parametrize(
    ("top_n", "replaced"),
    [
        *((7, replaced) for replaced in range(8)),
        *((3, replaced) for replaced in range(4)),
        *((5, replaced) for replaced in range(6)),
    ],
)
def test_selection_change_notional_is_exact_for_every_replacement_count(
    top_n, replaced
) -> None:
    transition = _selection_transition(top_n=top_n, replaced=replaced)
    assumptions = build_cost_assumptions(
        fee_rate="0", spread_cost_rate="0", slippage_rate="0"
    )
    result = CostAdjustedRankingEvaluationService._selection_cost(
        transition, assumptions
    )
    expected_replacement = Decimal(replaced) / Decimal(top_n)
    assert result.replacement_rate == expected_replacement
    assert result.sell_notional_ratio == expected_replacement
    assert result.buy_notional_ratio == expected_replacement
    assert result.gross_traded_notional_ratio == Decimal("2") * expected_replacement


def test_top_seven_two_replacements_uses_canonical_decimal_path() -> None:
    transition = _selection_transition(top_n=7, replaced=2)
    result = CostAdjustedRankingEvaluationService._selection_cost(
        transition,
        build_cost_assumptions(
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        ),
    )
    expected = Decimal("2") / Decimal("7")
    assert result.replacement_rate == expected
    assert result.sell_notional_ratio == expected
    assert result.buy_notional_ratio == expected
    assert result.gross_traded_notional_ratio == Decimal("2") * expected


def test_cost_ratio_converts_to_return_percentage_points() -> None:
    transition = _selection_transition(top_n=5, replaced=1)
    assumptions = build_cost_assumptions(
        fee_rate="0.0005", spread_cost_rate="0.0005", slippage_rate="0.001"
    )
    result = CostAdjustedRankingEvaluationService._selection_cost(
        transition, assumptions
    )
    assert assumptions.total_cost_rate == Decimal("0.002")
    assert result.gross_traded_notional_ratio == Decimal("0.4")
    assert result.execution_cost_ratio == Decimal("0.0008")
    assert result.execution_cost_percentage == Decimal("0.0800")


@pytest.mark.parametrize("value", ["-0.1", "NaN", "Infinity", "-Infinity", "1", True])
def test_invalid_cost_rate_is_rejected(value) -> None:
    with pytest.raises(ReplayInputError):
        build_cost_assumptions(fee_rate=value, spread_cost_rate="0", slippage_rate="0")


def test_adjusted_returns_apply_cost_to_both_sides_and_preserve_identity() -> None:
    definitions, turnover, matrix, *_ = _inputs()
    result = _evaluate(turnover, matrix, definitions)
    snapshot = result.cohorts[0].scenario_results[0].snapshots[0]
    assert result.status == SUCCESS
    assert snapshot.baseline_execution_cost_percentage == Decimal("0.2000")
    assert snapshot.scenario_execution_cost_percentage == Decimal("0.400")
    assert snapshot.baseline_cost_adjusted_return == Decimal("1.3000")
    assert snapshot.scenario_cost_adjusted_return == Decimal("1.600")
    assert snapshot.cost_adjusted_return_delta == Decimal("0.3000")
    assert snapshot.cost_adjusted_return_delta == snapshot.gross_return_delta - (
        snapshot.scenario_execution_cost_percentage
        - snapshot.baseline_execution_cost_percentage
    )


@pytest.mark.parametrize(
    ("baseline_replaced", "scenario_replaced", "relation"),
    [(1, 1, "equal"), (0, 1, "lower"), (1, 0, "higher")],
)
def test_cost_difference_changes_adjusted_delta_as_expected(
    baseline_replaced, scenario_replaced, relation
) -> None:
    definitions, turnover, matrix, *_ = _inputs(
        baseline_replaced=baseline_replaced,
        scenario_replaced=scenario_replaced,
    )
    snapshot = (
        _evaluate(turnover, matrix, definitions)
        .cohorts[0]
        .scenario_results[0]
        .snapshots[0]
    )
    if relation == "equal":
        assert snapshot.cost_adjusted_return_delta == snapshot.gross_return_delta
    elif relation == "lower":
        assert snapshot.cost_adjusted_return_delta < snapshot.gross_return_delta
    else:
        assert snapshot.cost_adjusted_return_delta > snapshot.gross_return_delta


def test_first_ab_snapshot_without_transition_is_excluded() -> None:
    definitions, turnover, matrix, *_ = _inputs(common_ids=(1, 2))
    result = _evaluate(turnover, matrix, definitions)
    cohort = result.cohorts[0]
    assert cohort.common_comparable_ab_snapshot_count == 2
    assert cohort.cost_adjustable_snapshot_count == 1
    assert cohort.cost_adjustable_coverage_rate == Decimal("0.5")
    assert cohort.scenario_results[0].snapshots[0].snapshot_id == 2


def test_continuity_break_current_id_does_not_overlap_ab_sample() -> None:
    definitions, turnover, matrix, *_ = _inputs(current_id=3, common_ids=(2,))
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == NO_COST_ADJUSTABLE_SNAPSHOTS
    assert result.cohorts[0].cost_adjustable_snapshot_count == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: replace(row, captured_at=datetime(2026, 1, 3, tzinfo=UTC)),
        lambda row: replace(row, baseline_policy_signature="other"),
        lambda row: replace(row, effective_top_n=3),
        lambda row: replace(row, scenario_signature="other"),
        lambda row: replace(row, baseline_top_markets=("wrong", "markets")),
        lambda row: replace(row, scenario_top_markets=("wrong", "markets")),
        lambda row: replace(row, horizon_minutes=240),
        lambda row: replace(row, baseline_mean_return=Decimal("NaN")),
    ],
)
def test_lineage_mismatch_fails_closed(mutation) -> None:
    definitions, turnover, matrix, _baseline, _scenario, row = _inputs()
    bad_row = mutation(row)
    bad_cohort = replace(
        matrix.cohorts[0],
        scenario_results=((definitions[0].name, (bad_row,)),),
    )
    result = _evaluate(turnover, replace(matrix, cohorts=(bad_cohort,)), definitions)
    assert result.status == INVALID_COST_ADJUSTED_DATA


def test_partial_scenario_produces_no_partial_average() -> None:
    definitions, turnover, matrix, *_ = _inputs(ab_status=OUTCOME_INCOMPLETE)
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == NO_COMMON_COMPARABLE_SNAPSHOTS
    assert result.cohorts[0].scenario_results[0].mean_gross_return_delta is None


@pytest.mark.parametrize(
    ("turnover_status", "expected"),
    [
        (INSUFFICIENT_TEMPORAL_TRANSITIONS, NO_TURNOVER_TRANSITIONS),
        (INVALID_TURNOVER_DATA, INVALID_COST_ADJUSTED_DATA),
    ],
)
def test_turnover_safe_and_invalid_statuses_are_mapped(
    turnover_status, expected
) -> None:
    definitions, turnover, matrix, *_ = _inputs(turnover_status=turnover_status)
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == expected


def test_same_turnover_can_be_applied_independently_to_two_horizons() -> None:
    definitions, turnover, matrix, *_ = _inputs()
    second_row = replace(
        matrix.cohorts[0].results_for(definitions[0].name)[0], horizon_minutes=240
    )
    second_cohort = replace(
        matrix.cohorts[0],
        horizon_minutes=240,
        scenario_results=((definitions[0].name, (second_row,)),),
    )
    matrix = replace(
        matrix, horizons=(60, 240), cohorts=(*matrix.cohorts, second_cohort)
    )
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == SUCCESS
    assert [cohort.horizon_minutes for cohort in result.cohorts] == [60, 240]
    assert all(cohort.cost_adjustable_snapshot_count == 1 for cohort in result.cohorts)


def test_summary_uses_exact_cost_adjustable_subset() -> None:
    definitions, turnover, matrix, *_ = _inputs()
    result = _evaluate(turnover, matrix, definitions)
    summary = result.cohorts[0].scenario_results[0]
    snapshot = summary.snapshots[0]
    assert summary.cost_adjusted_snapshot_count == 1
    assert summary.mean_baseline_gross_return == snapshot.baseline_gross_return
    assert summary.mean_scenario_gross_return == snapshot.scenario_gross_return
    assert summary.mean_gross_return_delta == snapshot.gross_return_delta
    assert summary.mean_baseline_execution_cost_percentage == Decimal("0.2000")
    assert summary.mean_scenario_execution_cost_percentage == Decimal("0.400")
    assert summary.mean_execution_cost_delta_percentage == Decimal("0.2000")
    assert summary.mean_cost_adjusted_return_delta == Decimal("0.3000")
    assert summary.median_cost_adjusted_return_delta == Decimal("0.3000")
    assert summary.cost_adjusted_scenario_win_count == 1
    assert summary.cost_adjusted_scenario_loss_count == 0
    assert summary.cost_adjusted_tie_count == 0
    assert summary.cost_adjusted_scenario_win_rate == 1


@pytest.mark.parametrize(
    ("scenario_return", "wins", "losses", "ties"),
    [
        (Decimal("2.0"), 1, 0, 0),
        (Decimal("1.7"), 0, 0, 1),
        (Decimal("1.0"), 0, 1, 0),
    ],
)
def test_adjusted_classification_is_snapshot_technical_comparison(
    scenario_return, wins, losses, ties
) -> None:
    definitions, turnover, matrix, *_ = _inputs(
        baseline_replaced=0, scenario_replaced=1
    )
    row = matrix.cohorts[0].results_for(definitions[0].name)[0]
    row = replace(
        row,
        scenario_mean_return=scenario_return,
        mean_return_delta=scenario_return - Decimal("1.5"),
        scenario_median_return=scenario_return,
        median_return_delta=scenario_return - Decimal("1.5"),
    )
    cohort = replace(
        matrix.cohorts[0],
        scenario_results=((definitions[0].name, (row,)),),
    )
    summary = (
        _evaluate(turnover, replace(matrix, cohorts=(cohort,)), definitions)
        .cohorts[0]
        .scenario_results[0]
    )
    assert summary.cost_adjusted_scenario_win_count == wins
    assert summary.cost_adjusted_scenario_loss_count == losses
    assert summary.cost_adjusted_tie_count == ties
    assert summary.cost_adjusted_scenario_win_rate == Decimal(wins)


def test_impossible_turnover_notional_identity_fails_closed() -> None:
    definitions, turnover, matrix, *_ = _inputs()
    temporal = turnover.cohorts[0].transitions[0]
    bad_temporal = replace(
        temporal, baseline=replace(temporal.baseline, exited_count=0)
    )
    turnover.cohorts[0].transitions = (bad_temporal,)
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == INVALID_COST_ADJUSTED_DATA


def test_turnover_scenario_definition_identity_mismatch_fails_closed() -> None:
    definitions, turnover, matrix, *_ = _inputs()
    turnover.cohorts[0].scenario_summaries[0].scenario_definition_signature = "wrong"
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == INVALID_COST_ADJUSTED_DATA


def test_scenario_turnover_pair_lineage_mismatch_fails_closed() -> None:
    definitions, turnover, matrix, *_ = _inputs()
    temporal = turnover.cohorts[0].transitions[0]
    scenario = temporal.scenarios[0]
    bad_scenario = replace(
        scenario,
        transition=replace(
            scenario.transition,
            previous_snapshot_id=scenario.transition.previous_snapshot_id - 1,
        ),
    )
    turnover.cohorts[0].transitions = (replace(temporal, scenarios=(bad_scenario,)),)
    result = _evaluate(turnover, matrix, definitions)
    assert result.status == INVALID_COST_ADJUSTED_DATA


def test_cli_requires_all_inputs_rejects_duplicates_and_reports_safety_flags() -> None:
    with pytest.raises(SystemExit):
        cli.parse_arguments([])
    with pytest.raises(SystemExit) as invalid_cost:
        cli.parse_arguments(
            [
                "--latest",
                "2",
                "--horizon",
                "60",
                "--scenario-file",
                "x",
                "--fee-rate",
                "NaN",
                "--spread-cost-rate",
                "0",
                "--slippage-rate",
                "0",
            ]
        )
    assert invalid_cost.value.code == 2
    with pytest.raises(SystemExit):
        cli.parse_arguments(
            [
                "--latest",
                "2",
                "--horizon",
                "60",
                "--horizon",
                "60",
                "--scenario-file",
                "x",
                "--fee-rate",
                "0",
                "--spread-cost-rate",
                "0",
                "--slippage-rate",
                "0",
            ]
        )
    definitions, turnover, matrix, *_ = _inputs()
    output = "\n".join(cli.report(_evaluate(turnover, matrix, definitions)))
    assert "actual_pnl_computed=false" in output
    assert "stored_spread_used=false" in output
    assert "cost_assumptions_explicit=true" in output
    assert "cost_rate_unit=FRACTION_OF_TRADED_NOTIONAL" in output
    assert "winner" not in output.lower()
    assert "promotion" not in output.lower()


def test_cli_exit_codes_are_zero_for_safe_states_and_one_for_integrity(
    monkeypatch,
) -> None:
    definitions, turnover, matrix, *_ = _inputs()
    base = _evaluate(turnover, matrix, definitions)
    namespace = SimpleNamespace(
        latest=5,
        horizon=[60],
        scenario_file="unused",
        fee_rate=Decimal("0"),
        spread_cost_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    )
    monkeypatch.setattr(cli, "load_scenario_file", lambda _path: definitions)
    service = MagicMock()
    monkeypatch.setattr(
        "crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service."
        "CostAdjustedRankingEvaluationService",
        lambda _session: service,
    )
    for status, expected in (
        (SUCCESS, 0),
        (NO_TURNOVER_TRANSITIONS, 0),
        (NO_COMMON_COMPARABLE_SNAPSHOTS, 0),
        (NO_COST_ADJUSTABLE_SNAPSHOTS, 0),
        (INVALID_COST_ADJUSTED_DATA, 1),
    ):
        service.evaluate.return_value = replace(base, status=status)
        _lines, exit_code = cli.run(MagicMock(), namespace)
        assert exit_code == expected
