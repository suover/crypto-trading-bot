from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    INVALID_COST_ADJUSTED_DATA,
)
from crypto_trading_bot.services.cost_adjusted_validation_robustness_service import (
    INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA,
    INVALID_COST_ADJUSTED_ROBUSTNESS_DATA,
    NO_COST_ADJUSTABLE_SNAPSHOTS,
    SUCCESS,
    CostAdjustedValidationRobustnessService,
    RobustnessDataError,
    distribution_statistics,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    CostAdjustedWalkForwardValidationService,
)
from tests.test_cost_adjusted_walk_forward_validation import (
    ASSUMPTIONS,
    _cohort,
    _scenarios,
    _source,
)


def _results(source, *, initial=2, validation=1):
    walk = CostAdjustedWalkForwardValidationService(MagicMock()).evaluate_from_result(
        source,
        initial_research_size=initial,
        validation_size=validation,
    )
    result = CostAdjustedValidationRobustnessService(MagicMock()).evaluate_from_results(
        source, walk
    )
    return result, walk


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), (0, 0, 0, 0, None)),
        (("4",), (1, 1, 0, 0, Decimal("1"))),
        (("1", "2"), (2, 2, 0, 0, Decimal("1"))),
        (("-1", "-2"), (2, 0, 2, 0, Decimal("0"))),
        (("0", "0"), (2, 0, 0, 2, Decimal("0"))),
        (("-2", "0", "1", "3"), (4, 2, 1, 1, Decimal("0.5"))),
    ],
)
def test_cost_adjusted_distribution_counts(values, expected):
    stats = distribution_statistics(Decimal(item) for item in values)
    assert (
        stats.count,
        stats.positive_count,
        stats.negative_count,
        stats.tie_count,
        stats.positive_rate,
    ) == expected


def test_cost_adjusted_distribution_mean_medians_range_pstdev_and_precision():
    odd = distribution_statistics(map(Decimal, ("-1", "2", "9")))
    even = distribution_statistics(map(Decimal, ("-1", "0", "2", "3")))
    precise = distribution_statistics(
        (
            Decimal("0.2888610489553571428571428575"),
            Decimal("-0.4355892422414285714285714285"),
        )
    )
    assert odd.mean_delta == Decimal("10") / Decimal("3")
    assert odd.median_delta == 2
    assert even.median_delta == 1
    assert (odd.min_delta, odd.max_delta, odd.delta_range) == (-1, 9, 10)
    assert even.delta_stddev == Decimal("1.581138830084189665999446772")
    assert precise.mean_delta == (
        Decimal("0.2888610489553571428571428575")
        + Decimal("-0.4355892422414285714285714285")
    ) / Decimal("2")


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_cost_adjusted_distribution_non_finite_is_invalid(value):
    with pytest.raises(RobustnessDataError):
        distribution_statistics((value,))


def test_fold_and_snapshot_robustness_are_validation_only():
    source = _source(
        _cohort(
            7,
            adjusted_deltas=("999", "888", "1", "-2", "0", "3", "777"),
        )
    )
    result, _ = _results(source, initial=2, validation=2)
    cohort = result.cohorts[0]
    scenario = cohort.scenario_results[0]
    assert result.status == cohort.status == SUCCESS
    assert cohort.validation_snapshot_count == 4
    assert cohort.unused_tail_snapshot_count == 1
    assert scenario.fold_statistics.statistics.count == 2
    assert scenario.fold_statistics.statistics.mean_delta == Decimal("0.5")
    assert scenario.fold_statistics.worst_fold_index == 1
    assert scenario.fold_statistics.best_fold_index == 2
    assert scenario.snapshot_statistics.statistics.count == 4
    assert scenario.snapshot_statistics.statistics.mean_delta == Decimal("0.5")
    assert scenario.snapshot_statistics.worst_snapshot_id == 4
    assert scenario.snapshot_statistics.best_snapshot_id == 6
    assert Decimal("999") not in {
        scenario.snapshot_statistics.statistics.min_delta,
        scenario.snapshot_statistics.statistics.max_delta,
    }
    assert Decimal("777") not in {
        scenario.snapshot_statistics.statistics.min_delta,
        scenario.snapshot_statistics.statistics.max_delta,
    }


def test_count_one_is_valid_descriptive_statistics_with_zero_pstdev():
    result, _ = _results(_source(_cohort(3)), initial=2, validation=1)
    scenario = result.cohorts[0].scenario_results[0]
    assert result.status == SUCCESS
    assert scenario.fold_statistics.statistics.count == 1
    assert scenario.fold_statistics.statistics.delta_stddev == 0
    assert scenario.snapshot_statistics.statistics.count == 1
    assert scenario.snapshot_statistics.statistics.delta_stddev == 0
    assert result.sample_sufficiency_assessed is False
    assert result.statistical_inference_performed is False
    assert result.policy_decision_performed is False


def test_top_seven_repeating_and_long_decimal_reach_robustness_unchanged():
    values = (
        "0.2888610489553571428571428575",
        "-0.4355892422414285714285714285",
        "1.084512680392428571428571428",
        "0.2857142857142857142857142857",
    )
    result, _ = _results(
        _source(_cohort(4, top_n=7, adjusted_deltas=values)),
        initial=2,
        validation=2,
    )
    stats = result.cohorts[0].scenario_results[0].snapshot_statistics.statistics
    assert result.status == SUCCESS
    assert result.cohorts[0].effective_top_n == 7
    assert stats.min_delta == Decimal(values[3])
    assert stats.max_delta == Decimal(values[2])
    assert stats.mean_delta == (Decimal(values[2]) + Decimal(values[3])) / Decimal("2")


def test_tied_extremes_choose_first_fold_and_canonical_snapshot():
    source = _source(_cohort(6, adjusted_deltas=("8", "8", "-2", "-2", "3", "3")))
    scenario = (
        _results(source, initial=2, validation=1)[0].cohorts[0].scenario_results[0]
    )
    assert scenario.fold_statistics.worst_fold_index == 1
    assert scenario.fold_statistics.best_fold_index == 3
    assert scenario.snapshot_statistics.worst_snapshot_id == 3
    assert scenario.snapshot_statistics.best_snapshot_id == 5


def test_evaluate_runs_cost_adjusted_once_and_reuses_same_result_for_walk_forward():
    source = _source(_cohort(4))
    evaluator = MagicMock()
    evaluator.evaluate.return_value = source
    walk = CostAdjustedWalkForwardValidationService(
        MagicMock(), cost_adjusted_service=evaluator
    )
    walk.evaluate = MagicMock(side_effect=AssertionError("must not call evaluate"))
    original_from_result = walk.evaluate_from_result
    walk.evaluate_from_result = MagicMock(side_effect=original_from_result)
    service = CostAdjustedValidationRobustnessService(
        MagicMock(), cost_adjusted_service=evaluator, walk_forward_service=walk
    )
    result = service.evaluate(
        scenarios=_scenarios(),
        horizons=(60,),
        latest=10,
        fee_rate="0.0005",
        spread_cost_rate="0.0005",
        slippage_rate="0.001",
        initial_research_size=2,
        validation_size=1,
    )
    assert result.status == SUCCESS
    evaluator.evaluate.assert_called_once()
    walk.evaluate.assert_not_called()
    walk.evaluate_from_result.assert_called_once_with(
        source, initial_research_size=2, validation_size=1
    )


def test_multiple_horizons_and_cohorts_are_isolated():
    source = _source(
        _cohort(5, horizon=60),
        _cohort(5, horizon=240),
        _cohort(2, horizon=1440),
        _cohort(5, horizon=60, baseline_signature="baseline-b"),
        _cohort(5, horizon=60, top_n=5),
    )
    result, _ = _results(source, initial=2, validation=1)
    by_horizon = {item.horizon_minutes: item.status for item in result.cohorts}
    assert result.status == SUCCESS
    assert by_horizon[60] == SUCCESS
    assert by_horizon[240] == SUCCESS
    assert by_horizon[1440] == INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA
    assert (
        len(
            {
                (
                    item.horizon_minutes,
                    item.baseline_policy_signature,
                    item.effective_top_n,
                )
                for item in result.cohorts
            }
        )
        == 5
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "horizon",
        "policy",
        "top_n",
        "candidate_count",
        "common_count",
        "cost_count",
        "coverage",
        "fold_count",
        "validation_size",
        "step_size",
        "assumptions",
    ],
)
def test_source_walk_forward_identity_mismatch_is_invalid(mutation):
    source = _source(_cohort(4))
    _, walk = _results(source)
    cohort = walk.cohorts[0]
    if mutation == "horizon":
        cohort = replace(cohort, horizon_minutes=240)
    elif mutation == "policy":
        cohort = replace(cohort, baseline_policy_signature="other")
    elif mutation == "top_n":
        cohort = replace(cohort, effective_top_n=8)
    elif mutation == "candidate_count":
        cohort = replace(cohort, candidate_ab_snapshot_count=99)
    elif mutation == "common_count":
        cohort = replace(cohort, common_comparable_ab_snapshot_count=99)
    elif mutation == "cost_count":
        cohort = replace(cohort, cost_adjustable_snapshot_count=99)
    elif mutation == "coverage":
        cohort = replace(cohort, cost_adjustable_coverage_rate=Decimal("0.1"))
    elif mutation == "fold_count":
        cohort = replace(cohort, fold_count=99)
    elif mutation == "validation_size":
        cohort = replace(cohort, validation_size=2)
    elif mutation == "step_size":
        cohort = replace(cohort, step_size=2)
    else:
        walk = replace(
            walk,
            assumptions=replace(ASSUMPTIONS, total_cost_rate=Decimal("0.003")),
        )
    if mutation != "assumptions":
        walk = replace(walk, cohorts=(cohort,))
    result = CostAdjustedValidationRobustnessService(MagicMock()).evaluate_from_results(
        source, walk
    )
    assert result.status == INVALID_COST_ADJUSTED_ROBUSTNESS_DATA
    assert result.policy_decision_performed is False


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_validation",
        "missing_snapshot",
        "scenario_sample",
        "scenario_definition",
        "scenario_signature",
        "naive_datetime",
        "chronology",
        "non_finite",
        "baseline",
        "fold_scenario_signature",
    ],
)
def test_validation_lineage_failure_is_invalid(mutation):
    source = _source(_cohort(5))
    _, walk = _results(source, initial=2, validation=1)
    if mutation == "duplicate_validation":
        folds = list(walk.cohorts[0].folds)
        folds[1] = replace(folds[1], validation_snapshot_ids=(3,))
        walk = replace(
            walk,
            cohorts=(replace(walk.cohorts[0], folds=tuple(folds)),),
        )
    elif mutation == "chronology":
        folds = list(walk.cohorts[0].folds)
        folds[0], folds[1] = folds[1], folds[0]
        folds = [replace(item, fold_index=index) for index, item in enumerate(folds, 1)]
        walk = replace(
            walk,
            cohorts=(replace(walk.cohorts[0], folds=tuple(folds)),),
        )
    elif mutation == "fold_scenario_signature":
        folds = list(walk.cohorts[0].folds)
        scenarios = list(folds[0].scenario_results)
        scenarios[0] = replace(scenarios[0], scenario_signature="wrong")
        folds[0] = replace(folds[0], scenario_results=tuple(scenarios))
        walk = replace(
            walk,
            cohorts=(replace(walk.cohorts[0], folds=tuple(folds)),),
        )
    else:
        cohort = source.cohorts[0]
        scenarios = list(cohort.scenario_results)
        rows = list(scenarios[1].snapshots)
        if mutation == "missing_snapshot":
            rows = rows[:-1]
            scenarios[1] = replace(
                scenarios[1],
                snapshots=tuple(rows),
                cost_adjusted_snapshot_count=len(rows),
            )
        elif mutation == "scenario_sample":
            rows[-1] = replace(rows[-1], snapshot_id=99, pipeline_run_id="pipeline-99")
        elif mutation == "scenario_definition":
            scenarios[1] = replace(scenarios[1], scenario_definition_signature="wrong")
        elif mutation == "scenario_signature":
            rows[-1] = replace(rows[-1], scenario_signature="wrong")
        elif mutation == "naive_datetime":
            rows[-1] = replace(rows[-1], captured_at=datetime(2026, 1, 1))
        elif mutation == "non_finite":
            rows[-1] = replace(rows[-1], cost_adjusted_return_delta=Decimal("Infinity"))
        else:
            rows[-1] = replace(rows[-1], baseline_gross_return=Decimal("999"))
        if mutation != "scenario_definition":
            scenarios[1] = replace(scenarios[1], snapshots=tuple(rows))
        source = replace(
            source,
            cohorts=(replace(cohort, scenario_results=tuple(scenarios)),),
        )
    result = CostAdjustedValidationRobustnessService(MagicMock()).evaluate_from_results(
        source, walk
    )
    assert result.status == INVALID_COST_ADJUSTED_ROBUSTNESS_DATA


def test_same_timestamp_snapshot_id_is_canonical_chronology():
    same = datetime(2026, 1, 1, tzinfo=UTC)
    times = (same, same, same, same + timedelta(hours=1))
    source = _source(_cohort(4, times=times, order=(2, 0, 1, 3)))
    result, _ = _results(source, initial=1, validation=1)
    assert result.status == SUCCESS
    assert result.cohorts[0].validation_snapshot_count == 3


@pytest.mark.parametrize(
    "status",
    [NO_COST_ADJUSTABLE_SNAPSHOTS, INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA],
)
def test_upstream_safe_status_remains_safe(status):
    source = (
        _source(_cohort(0))
        if status == NO_COST_ADJUSTABLE_SNAPSHOTS
        else _source(_cohort(2))
    )
    result, _ = _results(source, initial=2, validation=1)
    assert result.status == status
    assert result.cohorts[0].status == status
    assert result.cohorts[0].robustness_computed is False


def test_upstream_invalid_reason_is_preserved():
    source = _source(status=INVALID_COST_ADJUSTED_DATA)
    walk = CostAdjustedWalkForwardValidationService(MagicMock()).evaluate_from_result(
        source, initial_research_size=2, validation_size=1
    )
    result = CostAdjustedValidationRobustnessService(MagicMock()).evaluate_from_results(
        source, walk
    )
    assert result.status == INVALID_COST_ADJUSTED_ROBUSTNESS_DATA
    assert "upstream invalid" in result.safe_reason
