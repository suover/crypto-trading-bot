from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioComparableCohort,
    RankingScenarioEvaluationMatrix,
    parse_scenario_document,
)
from crypto_trading_bot.services.ranking_validation_robustness_service import (
    INVALID_ROBUSTNESS_DATA,
    RobustnessDataError,
    RankingValidationRobustnessService,
    distribution_statistics,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    INSUFFICIENT_WALK_FORWARD_DATA,
    INVALID_WALK_FORWARD_DATA,
    RankingWalkForwardValidationService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    OUTCOME_INCOMPLETE,
    StrategyABPerformanceService,
)


def scenarios():
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "scenario_a",
                    "component_weights": {
                        "liquidity": "0.20",
                        "trend_alignment": "0.20",
                        "momentum": "0.30",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
                {
                    "name": "scenario_b",
                    "component_weights": {
                        "liquidity": "0.30",
                        "trend_alignment": "0.20",
                        "momentum": "0.20",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
            ],
        }
    )


def result(snapshot_id, delta, *, scenario_name="scenario_a", status=SUCCESS):
    parsed = Decimal(delta) if delta is not None else None
    evaluated = status == SUCCESS
    baseline = Decimal(snapshot_id)
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=snapshot_id),
        horizon_minutes=60,
        baseline_policy_signature="baseline-a",
        scenario_signature=scenario_name,
        status=status,
        performance_evaluated=evaluated,
        effective_top_n=3,
        baseline_top_markets=("KRW-A", "KRW-B", "KRW-C"),
        baseline_mean_return=baseline if evaluated else None,
        scenario_mean_return=baseline + parsed
        if evaluated and parsed is not None
        else None,
        mean_return_delta=parsed if evaluated else None,
        baseline_median_return=baseline if evaluated else None,
        scenario_median_return=baseline + parsed
        if evaluated and parsed is not None
        else None,
        median_return_delta=parsed if evaluated else None,
        baseline_positive_rate=Decimal("0.5") if evaluated else None,
        scenario_positive_rate=Decimal("0.6") if evaluated else None,
        scenario_result=(
            None
            if not evaluated or parsed is None
            else "SCENARIO_WIN"
            if parsed > 0
            else "SCENARIO_LOSS"
            if parsed < 0
            else "TIE"
        ),
    )


def matrix(first_deltas, second_deltas=None, *, status=SUCCESS, common_ids=None):
    definitions = scenarios()
    second_deltas = first_deltas if second_deltas is None else second_deltas
    first = tuple(
        result(index, delta, scenario_name="scenario_a")
        for index, delta in enumerate(first_deltas, start=1)
    )
    second = tuple(
        result(index, delta, scenario_name="scenario_b")
        for index, delta in enumerate(second_deltas, start=1)
    )
    ids = tuple(range(1, len(first_deltas) + 1))
    common = ids if common_ids is None else tuple(common_ids)
    cohort = RankingScenarioComparableCohort(
        horizon_minutes=60,
        baseline_policy_signature="baseline-a" if ids else None,
        effective_top_n=3 if ids else 0,
        candidate_snapshot_ids=ids,
        common_snapshot_ids=common,
        common_coverage_rate=(
            Decimal(len(common)) / Decimal(len(ids)) if ids else Decimal("0")
        ),
        status=status,
        safe_reason=None if status == SUCCESS else "upstream safe state",
        scenario_results=((definitions[0].name, first), (definitions[1].name, second)),
    )
    return RankingScenarioEvaluationMatrix(
        requested_snapshot_count=max(len(ids), 1),
        evaluated_snapshot_count=len(ids),
        scenarios=definitions,
        horizons=(60,),
        cohorts=(cohort,),
    )


class CountingPerformanceService:
    def __init__(self):
        self.delegate = StrategyABPerformanceService(MagicMock())

    def summarize_results(self, requested_count, results):
        return self.delegate.summarize_results(requested_count, results)


class MatrixSweep:
    def __init__(self, value, performance_service):
        self.value = value
        self.performance_service = performance_service
        self.calls = 0

    def evaluate_matrix(self, **_):
        self.calls += 1
        return self.value


def service(value):
    performance = CountingPerformanceService()
    sweep = MatrixSweep(value, performance)
    walk = RankingWalkForwardValidationService(
        MagicMock(), sweep_service=sweep, performance_service=performance
    )
    robustness = RankingValidationRobustnessService(
        MagicMock(),
        sweep_service=sweep,
        walk_forward_service=walk,
        performance_service=performance,
    )
    return robustness, sweep


def test_empty_distribution() -> None:
    stats = distribution_statistics(())
    assert stats.count == stats.positive_count == stats.negative_count == 0
    assert stats.tie_count == 0
    assert all(
        value is None
        for value in (
            stats.positive_rate,
            stats.mean_delta,
            stats.median_delta,
            stats.min_delta,
            stats.max_delta,
            stats.delta_range,
            stats.delta_stddev,
        )
    )


def test_single_distribution_has_zero_population_stddev() -> None:
    stats = distribution_statistics((Decimal("1.234567890123456789"),))
    assert stats.count == stats.positive_count == 1
    assert stats.mean_delta == Decimal("1.234567890123456789")
    assert stats.delta_stddev == 0


@pytest.mark.parametrize(
    ("values", "median_value"),
    [
        (("-1", "0", "1"), "0"),
        (("-1", "0", "1", "2"), "0.5"),
    ],
)
def test_distribution_counts_rate_median_min_max_range_and_precision(
    values, median_value
) -> None:
    stats = distribution_statistics(tuple(Decimal(value) for value in values))
    assert stats.positive_count == sum(Decimal(value) > 0 for value in values)
    assert stats.negative_count == 1
    assert stats.tie_count == 1
    assert stats.positive_rate == Decimal(stats.positive_count) / Decimal(len(values))
    assert stats.median_delta == Decimal(median_value)
    assert stats.min_delta == -1
    assert stats.max_delta == Decimal(values[-1])
    assert stats.delta_range == stats.max_delta - stats.min_delta
    assert isinstance(stats.delta_stddev, Decimal)


def test_population_standard_deviation_is_decimal_pstdev() -> None:
    stats = distribution_statistics(
        tuple(Decimal(value) for value in ("1", "-1", "0", "2"))
    )
    assert stats.mean_delta == Decimal("0.5")
    assert stats.median_delta == Decimal("0.5")
    assert stats.delta_stddev == Decimal("1.118033988749894848204586834")


@pytest.mark.parametrize("values", [(Decimal("NaN"),), (Decimal("Infinity"),), (1,)])
def test_invalid_distribution_values_fail_closed(values) -> None:
    with pytest.raises(RobustnessDataError):
        distribution_statistics(values)


def test_fold_and_snapshot_statistics_use_validation_only_and_one_matrix() -> None:
    value = matrix(["999", "1", "-1", "0", "2"])
    robustness, sweep = service(value)
    output = robustness.evaluate(
        scenarios=value.scenarios,
        horizons=(60,),
        latest=5,
        initial_research_size=1,
        validation_size=1,
    )
    assert sweep.calls == 1
    cohort = output.cohorts[0]
    assert cohort.status == SUCCESS
    assert cohort.validation_snapshot_count == 4
    scenario = cohort.scenario_results[0]
    fold = scenario.fold_statistics
    assert (fold.statistics.positive_count, fold.statistics.negative_count) == (2, 1)
    assert fold.statistics.tie_count == 1
    assert fold.statistics.positive_rate == Decimal("0.5")
    assert fold.statistics.mean_delta == Decimal("0.5")
    assert fold.statistics.median_delta == Decimal("0.5")
    assert fold.statistics.min_delta == -1
    assert fold.statistics.max_delta == 2
    assert fold.statistics.delta_range == 3
    assert fold.worst_fold_index == 2
    assert fold.best_fold_index == 4
    snapshot = scenario.snapshot_statistics
    assert snapshot.statistics == fold.statistics
    assert snapshot.worst_snapshot_id == 3
    assert snapshot.best_snapshot_id == 5
    assert Decimal("999") not in {
        snapshot.statistics.min_delta,
        snapshot.statistics.max_delta,
    }
    assert output.policy_decision_performed is False
    assert output.statistical_inference_performed is False
    assert output.sample_sufficiency_assessed is False


def test_tied_extremes_choose_first_fold_and_canonical_snapshot() -> None:
    value = matrix(["0", "-2", "-2", "3", "3"])
    robustness, _ = service(value)
    scenario = (
        robustness.evaluate(
            scenarios=value.scenarios,
            horizons=(60,),
            latest=5,
            initial_research_size=1,
            validation_size=1,
        )
        .cohorts[0]
        .scenario_results[0]
    )
    assert scenario.fold_statistics.worst_fold_index == 1
    assert scenario.fold_statistics.best_fold_index == 3
    assert scenario.snapshot_statistics.worst_snapshot_id == 2
    assert scenario.snapshot_statistics.best_snapshot_id == 4


def test_one_fold_has_zero_stddev() -> None:
    value = matrix(["10", "2", "4"])
    robustness, _ = service(value)
    scenario = (
        robustness.evaluate(
            scenarios=value.scenarios,
            horizons=(60,),
            latest=3,
            initial_research_size=1,
            validation_size=2,
        )
        .cohorts[0]
        .scenario_results[0]
    )
    assert scenario.fold_statistics.statistics.delta_stddev == 0
    assert scenario.snapshot_statistics.statistics.delta_stddev == 1


def test_scenarios_keep_separate_snapshot_distributions() -> None:
    value = matrix(["0", "1", "2", "3"], ["0", "-1", "-2", "-3"])
    robustness, _ = service(value)
    first, second = (
        robustness.evaluate(
            scenarios=value.scenarios,
            horizons=(60,),
            latest=4,
            initial_research_size=1,
            validation_size=1,
        )
        .cohorts[0]
        .scenario_results
    )
    assert first.snapshot_statistics.statistics.mean_delta == 2
    assert second.snapshot_statistics.statistics.mean_delta == -2


def test_horizon_baseline_signature_and_top_n_cohorts_remain_isolated() -> None:
    base = matrix(["0", "1", "2"])

    def rekey(source, *, horizon=60, signature="baseline-a", top_n=3):
        scenario_results = tuple(
            (
                name,
                tuple(
                    SimpleNamespace(
                        **{
                            **vars(item),
                            "horizon_minutes": horizon,
                            "baseline_policy_signature": signature,
                            "effective_top_n": top_n,
                        }
                    )
                    for item in items
                ),
            )
            for name, items in source.scenario_results
        )
        return replace(
            source,
            horizon_minutes=horizon,
            baseline_policy_signature=signature,
            effective_top_n=top_n,
            scenario_results=scenario_results,
        )

    cohorts = (
        base.cohorts[0],
        rekey(base.cohorts[0], signature="baseline-b"),
        rekey(base.cohorts[0], top_n=5),
        rekey(base.cohorts[0], horizon=240),
    )
    value = replace(base, horizons=(60, 240), cohorts=cohorts)
    robustness, _ = service(value)
    output = robustness.evaluate(
        scenarios=value.scenarios,
        horizons=value.horizons,
        latest=3,
        initial_research_size=1,
        validation_size=1,
    )
    assert {
        (item.horizon_minutes, item.baseline_policy_signature, item.effective_top_n)
        for item in output.cohorts
    } == {
        (60, "baseline-a", 3),
        (60, "baseline-b", 3),
        (60, "baseline-a", 5),
        (240, "baseline-a", 3),
    }
    assert all(item.validation_snapshot_count == 2 for item in output.cohorts)


def test_unused_tail_and_research_snapshots_are_not_included() -> None:
    value = matrix(["100", "200", "1", "2", "3", "4", "999"])
    robustness, _ = service(value)
    cohort = robustness.evaluate(
        scenarios=value.scenarios,
        horizons=(60,),
        latest=7,
        initial_research_size=2,
        validation_size=2,
    ).cohorts[0]
    assert cohort.validation_snapshot_count == 4
    assert cohort.unused_tail_snapshot_count == 1
    stats = cohort.scenario_results[0].snapshot_statistics.statistics
    assert stats.count == 4
    assert stats.min_delta == 1
    assert stats.max_delta == 4


def test_duplicate_validation_snapshot_fails_closed() -> None:
    value = matrix(["0", "1", "2", "3", "4"])
    robustness, _ = service(value)
    walk = robustness.walk_forward_service.evaluate_from_matrix(
        value, initial_research_size=1, validation_size=2
    )
    second = replace(
        walk.cohorts[0].folds[1],
        validation_snapshot_ids=(2, 5),
    )
    corrupted = replace(
        walk,
        cohorts=(replace(walk.cohorts[0], folds=(walk.cohorts[0].folds[0], second)),),
    )
    robustness.walk_forward_service = SimpleNamespace(
        evaluate_from_matrix=MagicMock(return_value=corrupted)
    )
    cohort = robustness.evaluate_from_matrix(
        value, initial_research_size=1, validation_size=2
    ).cohorts[0]
    assert cohort.status == INVALID_ROBUSTNESS_DATA
    assert "multiple" in cohort.safe_reason
    assert cohort.scenario_results == ()


@pytest.mark.parametrize(
    "problem", ["missing", "non_success", "missing_delta", "naive"]
)
def test_invalid_validation_snapshot_result_fails_closed(problem) -> None:
    value = matrix(["0", "1", "2"])
    robustness, _ = service(value)
    valid_walk = robustness.walk_forward_service.evaluate_from_matrix(
        value, initial_research_size=1, validation_size=1
    )
    cohort = value.cohorts[0]
    first_name, first_rows = cohort.scenario_results[0]
    altered = list(first_rows)
    if problem == "missing":
        altered = altered[:-1]
    elif problem == "non_success":
        altered[-1] = result(3, "2", status=OUTCOME_INCOMPLETE)
    elif problem == "missing_delta":
        altered[-1] = result(3, None)
    else:
        altered[-1] = SimpleNamespace(
            **{**vars(altered[-1]), "captured_at": datetime(2026, 1, 1)}
        )
    changed = replace(
        cohort,
        scenario_results=(
            (first_name, tuple(altered)),
            cohort.scenario_results[1],
        ),
    )
    value = replace(value, cohorts=(changed,))
    robustness.walk_forward_service = SimpleNamespace(
        evaluate_from_matrix=MagicMock(return_value=valid_walk)
    )
    result_cohort = robustness.evaluate_from_matrix(
        value,
        initial_research_size=1,
        validation_size=1,
    ).cohorts[0]
    assert result_cohort.status == INVALID_ROBUSTNESS_DATA
    assert result_cohort.robustness_computed is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (NO_COMMON_COMPARABLE_SNAPSHOTS, NO_COMMON_COMPARABLE_SNAPSHOTS),
        (SUCCESS, INSUFFICIENT_WALK_FORWARD_DATA),
    ],
)
def test_upstream_safe_states_remain_safe(status, expected) -> None:
    common = () if status == NO_COMMON_COMPARABLE_SNAPSHOTS else (1, 2)
    value = matrix(["1", "2"] if common else [], status=status, common_ids=common)
    robustness, _ = service(value)
    cohort = robustness.evaluate(
        scenarios=value.scenarios,
        horizons=(60,),
        latest=2,
        initial_research_size=2,
        validation_size=1,
    ).cohorts[0]
    assert cohort.status == expected
    assert cohort.robustness_computed is False
    assert cohort.scenario_results == ()


def test_upstream_invalid_maps_to_invalid_robustness() -> None:
    value = matrix(["1", "2", "3"])
    robustness, _ = service(value)
    walk = robustness.walk_forward_service.evaluate_from_matrix(
        value, initial_research_size=1, validation_size=1
    )
    invalid = replace(
        walk,
        cohorts=(
            replace(
                walk.cohorts[0],
                status=INVALID_WALK_FORWARD_DATA,
                safe_reason="matrix mismatch",
                folds=(),
                fold_count=0,
            ),
        ),
    )
    robustness.walk_forward_service = SimpleNamespace(
        evaluate_from_matrix=MagicMock(return_value=invalid)
    )
    cohort = robustness.evaluate_from_matrix(
        value, initial_research_size=1, validation_size=1
    ).cohorts[0]
    assert cohort.status == INVALID_ROBUSTNESS_DATA
    assert cohort.safe_reason == "matrix mismatch"
