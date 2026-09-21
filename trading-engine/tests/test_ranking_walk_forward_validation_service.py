from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioSweepService,
    parse_scenario_document,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    INSUFFICIENT_WALK_FORWARD_DATA,
    INVALID_WALK_FORWARD_DATA,
    WalkForwardInputError,
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


def performance_result(
    snapshot_id,
    *,
    captured_at,
    delta="1",
    status=SUCCESS,
    baseline_signature="baseline-a",
    top_n=3,
    horizon=60,
):
    baseline = Decimal(snapshot_id)
    parsed_delta = Decimal(delta)
    evaluated = status == SUCCESS
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        captured_at=captured_at,
        horizon_minutes=horizon,
        baseline_policy_signature=baseline_signature,
        effective_top_n=top_n,
        baseline_top_markets=tuple(f"KRW-{index}" for index in range(top_n)),
        status=status,
        performance_evaluated=evaluated,
        baseline_mean_return=baseline if evaluated else None,
        scenario_mean_return=baseline + parsed_delta if evaluated else None,
        mean_return_delta=parsed_delta if evaluated else None,
        baseline_median_return=baseline if evaluated else None,
        scenario_median_return=baseline + parsed_delta if evaluated else None,
        median_return_delta=parsed_delta if evaluated else None,
        baseline_positive_rate=Decimal("0.5") if evaluated else None,
        scenario_positive_rate=Decimal("0.6") if evaluated else None,
        scenario_result=(
            "SCENARIO_WIN"
            if evaluated and parsed_delta > 0
            else "SCENARIO_LOSS"
            if evaluated and parsed_delta < 0
            else "TIE"
            if evaluated
            else None
        ),
    )


def rows(
    count,
    *,
    deltas=None,
    statuses=None,
    order=None,
    captured_times=None,
    baseline_signature="baseline-a",
    top_n=3,
    horizon=60,
    first_id=1,
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    deltas = deltas or [str(index) for index in range(1, count + 1)]
    statuses = statuses or [SUCCESS] * count
    captured_times = captured_times or [
        start + timedelta(hours=index) for index in range(count)
    ]
    values = tuple(
        performance_result(
            snapshot_id,
            captured_at=captured_times[offset],
            delta=deltas[offset],
            status=statuses[offset],
            baseline_signature=baseline_signature,
            top_n=top_n,
            horizon=horizon,
        )
        for offset, snapshot_id in enumerate(range(first_id, first_id + count))
    )
    return tuple(values[index] for index in (order or range(count)))


class FakePerformanceService:
    def __init__(self, batches):
        self.batches = batches
        self.summary = StrategyABPerformanceService(MagicMock())
        self.summary_calls = []

    def evaluate_latest(self, limit, *, horizon_minutes, overrides):
        return SimpleNamespace(
            results=self.batches[(horizon_minutes, overrides["liquidity"])]
        )

    def summarize_results(self, requested_count, results):
        values = tuple(results)
        self.summary_calls.append(
            (requested_count, tuple(x.snapshot_id for x in values))
        )
        return self.summary.summarize_results(requested_count, values)


def validator(batches):
    performance = FakePerformanceService(batches)
    sweep = RankingScenarioSweepService(MagicMock(), performance_service=performance)
    return (
        RankingWalkForwardValidationService(
            MagicMock(), sweep_service=sweep, performance_service=performance
        ),
        performance,
    )


def batches_for(first, second=None, *, horizon=60):
    return {
        (horizon, Decimal("0.20")): first,
        (horizon, Decimal("0.30")): second if second is not None else first,
    }


def test_expanding_window_exact_boundaries_non_overlap_and_summary_reuse() -> None:
    validation_service, performance = validator(batches_for(rows(100)))
    result = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=100,
        initial_research_size=40,
        validation_size=10,
    )
    cohort = result.cohorts[0]
    assert cohort.fold_count == 6
    assert cohort.unused_tail_snapshot_count == 0
    assert cohort.folds[0].research_snapshot_ids == tuple(range(1, 41))
    assert cohort.folds[0].validation_snapshot_ids == tuple(range(41, 51))
    assert cohort.folds[1].research_snapshot_ids == tuple(range(1, 51))
    assert cohort.folds[1].validation_snapshot_ids == tuple(range(51, 61))
    validation_sets = [set(fold.validation_snapshot_ids) for fold in cohort.folds]
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(validation_sets)
        for right in validation_sets[index + 1 :]
    )
    assert all(fold.validation_snapshot_count == 10 for fold in cohort.folds)
    assert set(cohort.folds[0].validation_snapshot_ids) <= set(
        cohort.folds[1].research_snapshot_ids
    )
    assert cohort.folds[0].research_end_at < cohort.folds[0].validation_start_at
    assert (40, tuple(range(1, 41))) in performance.summary_calls
    assert (10, tuple(range(41, 51))) in performance.summary_calls
    assert result.policy_decision_performed is False
    assert result.strict_unseen_validation == "not_verified"
    assert not hasattr(result, "winner")
    assert not hasattr(result, "promotion_candidate")


def test_only_full_folds_are_built_and_tail_is_reported() -> None:
    validation_service, _ = validator(batches_for(rows(67)))
    cohort = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=67,
        initial_research_size=40,
        validation_size=10,
    ).cohorts[0]
    assert cohort.fold_count == 2
    assert cohort.unused_tail_snapshot_count == 7
    assert cohort.folds[-1].validation_snapshot_ids == tuple(range(51, 61))
    assert 61 not in {
        item for fold in cohort.folds for item in fold.validation_snapshot_ids
    }


def test_random_order_and_same_timestamp_use_canonical_ordering() -> None:
    same = datetime(2026, 1, 1, tzinfo=UTC)
    source = rows(6, captured_times=[same] * 6, order=[5, 1, 4, 0, 3, 2])
    validation_service, _ = validator(batches_for(source))
    fold = (
        validation_service.evaluate(
            scenarios=scenarios(),
            horizons=(60,),
            latest=6,
            initial_research_size=2,
            validation_size=2,
        )
        .cohorts[0]
        .folds[0]
    )
    assert fold.research_snapshot_ids == (1, 2)
    assert fold.validation_snapshot_ids == (3, 4)
    assert (fold.research_end_at, 2) < (fold.validation_start_at, 3)


def test_common_intersection_excludes_partial_outcome_for_every_scenario() -> None:
    first = rows(6)
    second = rows(
        6, statuses=[SUCCESS, SUCCESS, OUTCOME_INCOMPLETE, SUCCESS, SUCCESS, SUCCESS]
    )
    validation_service, _ = validator(batches_for(first, second))
    cohort = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=6,
        initial_research_size=2,
        validation_size=2,
    ).cohorts[0]
    assert cohort.common_comparable_snapshot_count == 5
    assert cohort.fold_count == 1
    assert 3 not in cohort.folds[0].research_snapshot_ids
    assert 3 not in cohort.folds[0].validation_snapshot_ids
    assert all(
        item.validation.snapshot_count == 2 for item in cohort.folds[0].scenario_results
    )


def test_validation_fold_summaries_are_validation_only() -> None:
    deltas = ["10", "10", "1", "1", "-2", "-2", "0", "0"]
    validation_service, _ = validator(batches_for(rows(8, deltas=deltas)))
    result = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=8,
        initial_research_size=2,
        validation_size=2,
    )
    summary = result.cohorts[0].scenario_results[0]
    assert summary.validation_fold_count == 3
    assert summary.positive_validation_fold_count == 1
    assert summary.negative_validation_fold_count == 1
    assert summary.tie_validation_fold_count == 1
    assert summary.mean_validation_return_delta == Decimal(
        "-0.3333333333333333333333333333"
    )
    assert summary.median_validation_return_delta == 0


def test_research_good_validation_bad_does_not_select_or_promote() -> None:
    validation_service, _ = validator(
        batches_for(rows(6, deltas=["10", "10", "-5", "-5", "-4", "-4"]))
    )
    result = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=6,
        initial_research_size=2,
        validation_size=2,
    )
    first = result.cohorts[0].folds[0].scenario_results[0]
    assert first.research.mean_return_delta > 0 > first.validation.mean_return_delta
    assert result.policy_decision_performed is False
    assert not hasattr(result, "recommended_scenario")


def test_signature_top_n_and_horizon_are_isolated() -> None:
    definitions = scenarios()
    batches = {}
    for horizon in (60, 240):
        mixed = (
            *rows(4, horizon=horizon),
            *rows(4, baseline_signature="baseline-b", first_id=5, horizon=horizon),
            *rows(4, top_n=5, first_id=9, horizon=horizon),
        )
        batches[(horizon, Decimal("0.20"))] = mixed
        batches[(horizon, Decimal("0.30"))] = mixed
    validation_service, _ = validator(batches)
    result = validation_service.evaluate(
        scenarios=definitions,
        horizons=(60, 240),
        latest=12,
        initial_research_size=2,
        validation_size=2,
    )
    assert result.cohort_count == 6
    assert (
        len(
            {
                (x.horizon_minutes, x.baseline_policy_signature, x.effective_top_n)
                for x in result.cohorts
            }
        )
        == 6
    )
    assert all(cohort.fold_count == 1 for cohort in result.cohorts)


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, NO_COMMON_COMPARABLE_SNAPSHOTS), (3, INSUFFICIENT_WALK_FORWARD_DATA)],
)
def test_no_common_and_insufficient_are_safe_reports(count, expected) -> None:
    validation_service, _ = validator(batches_for(rows(count)))
    cohort = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=10,
        initial_research_size=2,
        validation_size=2,
    ).cohorts[0]
    assert cohort.status == expected
    assert cohort.fold_count == 0
    assert cohort.performance_compared is False


def test_naive_temporal_metadata_fails_closed() -> None:
    validation_service, _ = validator(
        batches_for(rows(4, captured_times=[datetime(2026, 1, 1)] * 4))
    )
    cohort = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=4,
        initial_research_size=2,
        validation_size=2,
    ).cohorts[0]
    assert cohort.status == INVALID_WALK_FORWARD_DATA
    assert cohort.performance_compared is False
    assert cohort.fold_count == 0


def test_shared_matrix_integrity_failure_maps_to_walk_forward_invalid() -> None:
    first = rows(4)
    second = list(rows(4))
    second[0] = performance_result(
        1,
        captured_at=second[0].captured_at,
        baseline_signature="different-baseline",
    )
    validation_service, _ = validator(batches_for(first, tuple(second)))
    result = validation_service.evaluate(
        scenarios=scenarios(),
        horizons=(60,),
        latest=4,
        initial_research_size=2,
        validation_size=2,
    )
    assert any(cohort.status == INVALID_WALK_FORWARD_DATA for cohort in result.cohorts)
    assert all(
        not cohort.performance_compared
        for cohort in result.cohorts
        if cohort.status == INVALID_WALK_FORWARD_DATA
    )


@pytest.mark.parametrize("field", ["initial_research_size", "validation_size"])
@pytest.mark.parametrize("value", [0, -1, True])
def test_sizes_must_be_positive_non_boolean_integers(field, value) -> None:
    validation_service, _ = validator(batches_for(rows(4)))
    arguments = {"initial_research_size": 2, "validation_size": 2}
    arguments[field] = value
    with pytest.raises(WalkForwardInputError):
        validation_service.evaluate(
            scenarios=scenarios(), horizons=(60,), latest=4, **arguments
        )


@pytest.mark.parametrize(("latest", "horizons"), [(0, (60,)), (4, (0,))])
def test_matrix_input_validation_is_preserved(latest, horizons) -> None:
    validation_service, _ = validator(batches_for(rows(4)))
    with pytest.raises(ReplayInputError):
        validation_service.evaluate(
            scenarios=scenarios(),
            horizons=horizons,
            latest=latest,
            initial_research_size=2,
            validation_size=2,
        )
