from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_COST_ADJUSTED_DATA,
    SUCCESS as COST_SUCCESS,
    CostAdjustedRankingCohortResult,
    CostAdjustedRankingEvaluationResult,
    CostAdjustedRankingScenarioResult,
    CostAdjustedRankingSnapshotResult,
    CostAssumptions,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA,
    INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
    NO_COST_ADJUSTABLE_SNAPSHOTS,
    SUCCESS,
    CostAdjustedWalkForwardInputError,
    CostAdjustedWalkForwardValidationService,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)


def _scenarios():
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "first",
                    "component_weights": {
                        "liquidity": "0.35",
                        "trend_alignment": "0.20",
                        "momentum": "0.15",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
                {
                    "name": "second",
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
            ],
        }
    )


ASSUMPTIONS = CostAssumptions(
    fee_rate=Decimal("0.0005"),
    spread_cost_rate=Decimal("0.0005"),
    slippage_rate=Decimal("0.001"),
    total_cost_rate=Decimal("0.002"),
)


def _snapshot(
    snapshot_id: int,
    scenario,
    *,
    captured_at: datetime,
    gross_delta: Decimal,
    adjusted_delta: Decimal,
    horizon: int = 60,
    baseline_signature: str = "baseline-a",
    top_n: int = 7,
) -> CostAdjustedRankingSnapshotResult:
    baseline_return = Decimal("0.2888610489553571428571428575") + Decimal(
        snapshot_id
    ) / Decimal("100")
    baseline_cost = Decimal("0.1142857142857142857142857143")
    scenario_cost = Decimal("0.1714285714285714285714285714")
    baseline_adjusted = baseline_return - baseline_cost
    return CostAdjustedRankingSnapshotResult(
        snapshot_id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        captured_at=captured_at,
        horizon_minutes=horizon,
        baseline_policy_signature=baseline_signature,
        effective_top_n=top_n,
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        scenario_signature=f"signature-{scenario.name}",
        fee_rate=ASSUMPTIONS.fee_rate,
        spread_cost_rate=ASSUMPTIONS.spread_cost_rate,
        slippage_rate=ASSUMPTIONS.slippage_rate,
        total_cost_rate=ASSUMPTIONS.total_cost_rate,
        target_weight=Decimal("1") / Decimal(top_n),
        baseline_replacement_rate=Decimal("2") / Decimal(top_n),
        baseline_sell_notional_ratio=Decimal("2") / Decimal(top_n),
        baseline_buy_notional_ratio=Decimal("2") / Decimal(top_n),
        baseline_gross_traded_notional_ratio=Decimal("4") / Decimal(top_n),
        baseline_execution_cost_ratio=baseline_cost / Decimal("100"),
        baseline_execution_cost_percentage=baseline_cost,
        scenario_replacement_rate=Decimal("3") / Decimal(top_n),
        scenario_sell_notional_ratio=Decimal("3") / Decimal(top_n),
        scenario_buy_notional_ratio=Decimal("3") / Decimal(top_n),
        scenario_gross_traded_notional_ratio=Decimal("6") / Decimal(top_n),
        scenario_execution_cost_ratio=scenario_cost / Decimal("100"),
        scenario_execution_cost_percentage=scenario_cost,
        baseline_gross_return=baseline_return,
        scenario_gross_return=baseline_return + gross_delta,
        gross_return_delta=gross_delta,
        baseline_cost_adjusted_return=baseline_adjusted,
        scenario_cost_adjusted_return=baseline_adjusted + adjusted_delta,
        cost_adjusted_return_delta=adjusted_delta,
    )


def _scenario_result(scenario, snapshots):
    values = tuple(snapshots)
    return CostAdjustedRankingScenarioResult(
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        scenario_signature=f"signature-{scenario.name}" if values else None,
        cost_adjusted_snapshot_count=len(values),
        mean_baseline_gross_return=None,
        mean_scenario_gross_return=None,
        mean_gross_return_delta=None,
        mean_baseline_gross_traded_notional_ratio=None,
        mean_scenario_gross_traded_notional_ratio=None,
        mean_baseline_execution_cost_percentage=None,
        mean_scenario_execution_cost_percentage=None,
        mean_execution_cost_delta_percentage=None,
        mean_baseline_cost_adjusted_return=None,
        mean_scenario_cost_adjusted_return=None,
        mean_cost_adjusted_return_delta=None,
        median_cost_adjusted_return_delta=None,
        cost_adjusted_scenario_win_count=0,
        cost_adjusted_scenario_loss_count=0,
        cost_adjusted_tie_count=0,
        cost_adjusted_scenario_win_rate=None,
        snapshots=values,
    )


def _cohort(
    count: int,
    *,
    horizon: int = 60,
    baseline_signature: str = "baseline-a",
    top_n: int = 7,
    times: tuple[datetime, ...] | None = None,
    order: tuple[int, ...] | None = None,
    gross_deltas: tuple[str, ...] | None = None,
    adjusted_deltas: tuple[str, ...] | None = None,
) -> CostAdjustedRankingCohortResult:
    scenarios = _scenarios()
    timestamps = times or tuple(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
        for index in range(count)
    )
    gross = gross_deltas or tuple(str(index + 1) for index in range(count))
    adjusted = adjusted_deltas or gross
    rows = []
    source_order = order or tuple(range(count))
    for scenario in scenarios:
        scenario_rows = tuple(
            _snapshot(
                index + 1,
                scenario,
                captured_at=timestamps[index],
                gross_delta=Decimal(gross[index]),
                adjusted_delta=Decimal(adjusted[index]),
                horizon=horizon,
                baseline_signature=baseline_signature,
                top_n=top_n,
            )
            for index in source_order
        )
        rows.append(_scenario_result(scenario, scenario_rows))
    status = COST_SUCCESS if count else NO_COST_ADJUSTABLE_SNAPSHOTS
    return CostAdjustedRankingCohortResult(
        horizon_minutes=horizon,
        baseline_policy_signature=baseline_signature,
        effective_top_n=top_n,
        candidate_ab_snapshot_count=count + 1,
        common_comparable_ab_snapshot_count=count + 1,
        turnover_transition_count=count,
        cost_adjustable_snapshot_count=count,
        cost_adjustable_coverage_rate=(
            Decimal(count) / Decimal(count + 1) if count else Decimal("0")
        ),
        status=status,
        safe_reason=None if count else "no overlap",
        performance_compared=bool(count),
        scenario_results=tuple(rows),
    )


def _source(*cohorts, status=None) -> CostAdjustedRankingEvaluationResult:
    values = tuple(cohorts) if cohorts else (_cohort(8),)
    scenarios = _scenarios()
    resolved_status = status or (
        COST_SUCCESS
        if any(item.status == COST_SUCCESS for item in values)
        else NO_COST_ADJUSTABLE_SNAPSHOTS
    )
    return CostAdjustedRankingEvaluationResult(
        requested_snapshot_count=20,
        evaluated_snapshot_count=18,
        scenario_count=len(scenarios),
        horizon_count=len({item.horizon_minutes for item in values}),
        cohort_count=len(values),
        status=resolved_status,
        safe_reason="upstream invalid"
        if resolved_status == INVALID_COST_ADJUSTED_DATA
        else None,
        assumptions=ASSUMPTIONS,
        scenarios=scenarios,
        cohorts=values,
    )


def _service(source=None):
    evaluator = MagicMock()
    evaluator.evaluate.return_value = source or _source()
    return (
        CostAdjustedWalkForwardValidationService(
            MagicMock(), cost_adjusted_service=evaluator
        ),
        evaluator,
    )


def test_evaluate_calls_cost_adjusted_once_and_uses_only_cost_adjustable_sample():
    service, evaluator = _service(_source(_cohort(3)))
    result = service.evaluate(
        scenarios=_scenarios(),
        horizons=(60,),
        latest=20,
        fee_rate="0.0005",
        spread_cost_rate="0.0005",
        slippage_rate="0.001",
        initial_research_size=1,
        validation_size=1,
    )
    evaluator.evaluate.assert_called_once()
    cohort = result.cohorts[0]
    assert cohort.candidate_ab_snapshot_count == 4
    assert cohort.common_comparable_ab_snapshot_count == 4
    assert cohort.cost_adjustable_snapshot_count == 3
    assert cohort.folds[0].research_snapshot_ids == (1,)
    assert cohort.folds[0].validation_snapshot_ids == (2,)
    assert 4 not in {
        item
        for fold in cohort.folds
        for item in (*fold.research_snapshot_ids, *fold.validation_snapshot_ids)
    }


def test_expanding_windows_validation_non_overlap_and_partial_tail():
    result = _service()[0].evaluate_from_result(
        _source(_cohort(8)), initial_research_size=3, validation_size=2
    )
    cohort = result.cohorts[0]
    assert result.status == SUCCESS
    assert result.step_size == cohort.step_size == 2
    assert cohort.fold_count == 2
    assert cohort.unused_tail_snapshot_count == 1
    assert cohort.folds[0].research_snapshot_ids == (1, 2, 3)
    assert cohort.folds[0].validation_snapshot_ids == (4, 5)
    assert cohort.folds[1].research_snapshot_ids == (1, 2, 3, 4, 5)
    assert cohort.folds[1].validation_snapshot_ids == (6, 7)
    assert set(cohort.folds[0].validation_snapshot_ids).isdisjoint(
        cohort.folds[1].validation_snapshot_ids
    )
    assert 8 not in {
        snapshot_id
        for fold in cohort.folds
        for snapshot_id in fold.validation_snapshot_ids
    }


def test_chronology_is_utc_timestamp_then_snapshot_id():
    same = datetime(2026, 1, 1, tzinfo=UTC)
    cohort = _cohort(
        5,
        times=(same, same, same, same + timedelta(hours=1), same + timedelta(hours=2)),
        order=(2, 0, 1, 4, 3),
    )
    fold = (
        _service()[0]
        .evaluate_from_result(
            _source(cohort), initial_research_size=2, validation_size=2
        )
        .cohorts[0]
        .folds[0]
    )
    assert fold.research_snapshot_ids == (1, 2)
    assert fold.validation_snapshot_ids == (3, 4)
    assert (fold.research_end_at, 2) < (fold.validation_start_at, 3)


def test_aggregates_use_canonical_snapshot_deltas_and_preserve_boundary_cost():
    cohort = _cohort(
        6,
        gross_deltas=("10", "4", "2", "8", "-2", "100"),
        adjusted_deltas=("9", "3", "1", "7", "-3", "99"),
    )
    fold = (
        _service()[0]
        .evaluate_from_result(
            _source(cohort), initial_research_size=3, validation_size=2
        )
        .cohorts[0]
        .folds[0]
    )
    period = fold.scenario_results[0]
    assert period.research.mean_gross_return_delta == Decimal("16") / Decimal("3")
    assert period.research.median_gross_return_delta == 4
    assert period.research.mean_cost_adjusted_return_delta == Decimal("13") / Decimal(
        "3"
    )
    assert period.validation.mean_gross_return_delta == 3
    assert period.validation.median_gross_return_delta == 3
    assert period.validation.mean_cost_adjusted_return_delta == 2
    assert period.validation.median_cost_adjusted_return_delta == 2
    assert period.validation.positive_cost_adjusted_snapshot_count == 1
    assert period.validation.negative_cost_adjusted_snapshot_count == 1
    assert period.validation.tie_cost_adjusted_snapshot_count == 0
    assert period.validation.positive_cost_adjusted_snapshot_rate == Decimal("0.5")
    source_snapshot = cohort.scenario_results[0].snapshots[3]
    assert source_snapshot.scenario_execution_cost_percentage > 0
    assert period.validation.mean_cost_adjusted_return_delta == (
        source_snapshot.cost_adjusted_return_delta
        + cohort.scenario_results[0].snapshots[4].cost_adjusted_return_delta
    ) / Decimal("2")


def test_fold_level_and_snapshot_level_validation_summaries():
    cohort = _cohort(
        8,
        gross_deltas=("0",) * 8,
        adjusted_deltas=("5", "5", "1", "1", "-2", "-2", "0", "0"),
    )
    summary = (
        _service()[0]
        .evaluate_from_result(
            _source(cohort), initial_research_size=2, validation_size=2
        )
        .cohorts[0]
        .scenario_results[0]
    )
    assert summary.validation_fold_count == 3
    assert summary.positive_validation_fold_count == 1
    assert summary.negative_validation_fold_count == 1
    assert summary.tie_validation_fold_count == 1
    assert summary.mean_validation_cost_adjusted_return_delta == Decimal(
        "-0.3333333333333333333333333333"
    )
    assert summary.median_validation_cost_adjusted_return_delta == 0
    assert summary.validation_snapshot_count == 6
    assert summary.positive_validation_snapshot_count == 2
    assert summary.negative_validation_snapshot_count == 2
    assert summary.tie_validation_snapshot_count == 2
    assert summary.positive_validation_snapshot_rate == Decimal("1") / Decimal("3")


def test_top_seven_repeating_and_long_decimal_values_remain_finite():
    long_values = (
        "0.2888610489553571428571428575",
        "-0.4355892422414285714285714285",
        "1.084512680392428571428571428",
        "0.2857142857142857142857142857",
    )
    result = _service()[0].evaluate_from_result(
        _source(_cohort(4, adjusted_deltas=long_values)),
        initial_research_size=2,
        validation_size=2,
    )
    period = result.cohorts[0].folds[0].scenario_results[0].validation
    assert result.status == SUCCESS
    assert period.mean_cost_adjusted_return_delta == (
        Decimal(long_values[2]) + Decimal(long_values[3])
    ) / Decimal("2")
    assert period.mean_cost_adjusted_return_delta.is_finite()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_snapshot",
        "naive_datetime",
        "scenario_snapshot_set",
        "scenario_signature",
        "scenario_definition_signature",
        "baseline_metadata",
        "assumption",
        "non_finite_gross",
        "non_finite_adjusted",
        "count_mismatch",
    ],
)
def test_integrity_failures_are_fail_closed(mutation):
    source = _source(_cohort(4))
    cohort = source.cohorts[0]
    scenarios = list(cohort.scenario_results)
    rows = list(scenarios[1].snapshots)
    if mutation == "duplicate_snapshot":
        rows[1] = replace(rows[1], snapshot_id=rows[0].snapshot_id)
    elif mutation == "naive_datetime":
        rows[0] = replace(rows[0], captured_at=datetime(2026, 1, 1))
    elif mutation == "scenario_snapshot_set":
        rows[0] = replace(rows[0], snapshot_id=999, pipeline_run_id="pipeline-999")
    elif mutation == "scenario_signature":
        rows[0] = replace(rows[0], scenario_signature="wrong")
    elif mutation == "scenario_definition_signature":
        rows[0] = replace(rows[0], scenario_definition_signature="wrong")
    elif mutation == "baseline_metadata":
        rows[0] = replace(rows[0], baseline_execution_cost_percentage=Decimal("9"))
    elif mutation == "assumption":
        rows[0] = replace(rows[0], total_cost_rate=Decimal("0.003"))
    elif mutation == "non_finite_gross":
        rows[0] = replace(rows[0], gross_return_delta=Decimal("NaN"))
    elif mutation == "non_finite_adjusted":
        rows[0] = replace(rows[0], cost_adjusted_return_delta=Decimal("Infinity"))
    scenarios[1] = replace(scenarios[1], snapshots=tuple(rows))
    invalid = replace(cohort, scenario_results=tuple(scenarios))
    if mutation == "count_mismatch":
        invalid = replace(invalid, cost_adjustable_snapshot_count=5)

    result = _service()[0].evaluate_from_result(
        _source(invalid), initial_research_size=2, validation_size=2
    )
    assert result.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA
    assert result.cohorts[0].status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA
    assert result.cohorts[0].performance_compared is False


def test_horizon_policy_and_top_n_cohorts_remain_isolated():
    source = _source(
        _cohort(4, horizon=60),
        _cohort(4, horizon=240),
        _cohort(4, horizon=60, baseline_signature="baseline-b"),
        _cohort(4, horizon=60, top_n=5),
        _cohort(3, horizon=1440),
    )
    result = _service()[0].evaluate_from_result(
        source, initial_research_size=2, validation_size=2
    )
    identities = {
        (item.horizon_minutes, item.baseline_policy_signature, item.effective_top_n)
        for item in result.cohorts
    }
    assert result.status == SUCCESS
    assert len(identities) == 5
    assert all(
        item.fold_count == 1 for item in result.cohorts if item.horizon_minutes != 1440
    )
    long_horizon = next(item for item in result.cohorts if item.horizon_minutes == 1440)
    assert long_horizon.status == INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA


def test_invalid_cohort_does_not_mix_with_or_erase_valid_cohort():
    valid = _cohort(4, horizon=60)
    invalid = _cohort(4, horizon=240)
    scenario_results = list(invalid.scenario_results)
    rows = list(scenario_results[0].snapshots)
    rows[0] = replace(rows[0], total_cost_rate=Decimal("0.003"))
    scenario_results[0] = replace(scenario_results[0], snapshots=tuple(rows))
    invalid = replace(invalid, scenario_results=tuple(scenario_results))

    result = _service()[0].evaluate_from_result(
        _source(valid, invalid), initial_research_size=2, validation_size=2
    )
    assert result.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA
    assert len(result.cohorts) == 2
    assert result.cohorts[0].status == SUCCESS
    assert result.cohorts[1].status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA


@pytest.mark.parametrize("count", [1, 2, 3])
def test_insufficient_sizes_and_partial_tail_are_safe(count):
    cohort = (
        _service()[0]
        .evaluate_from_result(
            _source(_cohort(count)), initial_research_size=2, validation_size=2
        )
        .cohorts[0]
    )
    assert cohort.status == INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA
    assert cohort.fold_count == 0
    assert cohort.performance_compared is False


def test_zero_cost_adjustable_snapshots_is_safe():
    cohort = (
        _service()[0]
        .evaluate_from_result(
            _source(_cohort(0)), initial_research_size=2, validation_size=2
        )
        .cohorts[0]
    )
    assert cohort.status == NO_COST_ADJUSTABLE_SNAPSHOTS
    assert cohort.fold_count == 0


def test_upstream_invalid_maps_to_walk_forward_invalid_and_preserves_reason():
    result = _service()[0].evaluate_from_result(
        _source(status=INVALID_COST_ADJUSTED_DATA),
        initial_research_size=2,
        validation_size=2,
    )
    assert result.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA
    assert "upstream invalid" in result.safe_reason


@pytest.mark.parametrize("field", ["initial_research_size", "validation_size"])
@pytest.mark.parametrize("value", [0, -1, True, Decimal("2")])
def test_fold_sizes_must_be_positive_non_boolean_integers(field, value):
    arguments = {"initial_research_size": 2, "validation_size": 2}
    arguments[field] = value
    with pytest.raises(CostAdjustedWalkForwardInputError):
        _service()[0].evaluate_from_result(_source(), **arguments)


def test_metric_types_are_preserved():
    assert GROSS_PERFORMANCE_METRIC_TYPE == "RANKING_SELECTION_GROSS_MARKET_PERFORMANCE"
    assert (
        COST_ADJUSTED_METRIC_TYPE == "COST_ADJUSTED_RANKING_SELECTION_PERFORMANCE_PROXY"
    )
