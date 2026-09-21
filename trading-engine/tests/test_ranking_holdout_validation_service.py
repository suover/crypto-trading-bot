from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.ranking_holdout_validation_service import (
    FIXED_TEMPORAL_CUTOFF,
    INSUFFICIENT_TEMPORAL_SPLIT_DATA,
    INVALID_HOLDOUT_DATA,
    TEMPORAL_RATIO,
    HoldoutInputError,
    RankingHoldoutValidationService,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioSweepService,
    parse_scenario_document,
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
    baseline_mean = Decimal(snapshot_id)
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
        baseline_mean_return=baseline_mean if evaluated else None,
        scenario_mean_return=(baseline_mean + parsed_delta) if evaluated else None,
        mean_return_delta=parsed_delta if evaluated else None,
        baseline_median_return=baseline_mean if evaluated else None,
        scenario_median_return=(baseline_mean + parsed_delta) if evaluated else None,
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


class FakePerformanceService:
    def __init__(self, batches):
        self.batches = batches
        self.calls = []
        self.summary = StrategyABPerformanceService(MagicMock())

    def evaluate_latest(self, limit, *, horizon_minutes, overrides):
        key = (horizon_minutes, overrides["liquidity"])
        self.calls.append((limit, key))
        return SimpleNamespace(results=self.batches[key])

    def summarize_results(self, requested_count, results):
        return self.summary.summarize_results(requested_count, results)


def service(batches):
    performance = FakePerformanceService(batches)
    sweep = RankingScenarioSweepService(MagicMock(), performance_service=performance)
    return RankingHoldoutValidationService(
        MagicMock(), sweep_service=sweep
    ), performance


def rows(
    *,
    count,
    liquidity,
    deltas=None,
    statuses=None,
    order=None,
    start=None,
    baseline_signature="baseline-a",
    top_n=3,
    horizon=60,
    first_id=1,
):
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    deltas = deltas or [str(index) for index in range(1, count + 1)]
    statuses = statuses or [SUCCESS] * count
    values = [
        performance_result(
            snapshot_id,
            captured_at=start + timedelta(hours=offset + 1),
            delta=deltas[offset],
            status=statuses[offset],
            baseline_signature=baseline_signature,
            top_n=top_n,
            horizon=horizon,
        )
        for offset, snapshot_id in enumerate(range(first_id, first_id + count))
    ]
    chosen_order = order or list(range(count))
    return tuple(values[index] for index in chosen_order)


def test_ratio_split_uses_oldest_seven_and_newest_three_after_sorting() -> None:
    definitions = scenarios()
    descending = list(reversed(range(10)))
    batches = {
        (60, Decimal("0.20")): rows(count=10, liquidity="0.20", order=descending),
        (60, Decimal("0.30")): rows(
            count=10,
            liquidity="0.30",
            deltas=[str(value * 2) for value in range(1, 11)],
            order=[4, 1, 9, 0, 8, 3, 7, 2, 6, 5],
        ),
    }
    validator, performance = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=10,
        holdout_ratio=Decimal("0.30"),
    ).cohorts[0]
    assert cohort.split_mode == TEMPORAL_RATIO
    assert (cohort.research_snapshot_count, cohort.holdout_snapshot_count) == (7, 3)
    assert cohort.scenario_results[0].research.mean_return_delta == Decimal("4")
    assert cohort.scenario_results[0].holdout.mean_return_delta == Decimal("9")
    assert cohort.research_start_at < cohort.research_end_at < cohort.holdout_start_at
    assert len(performance.calls) == 2


def test_same_captured_at_uses_snapshot_id_as_deterministic_tie_breaker() -> None:
    definitions = scenarios()
    same_time = datetime(2026, 1, 1, tzinfo=UTC)
    unordered = tuple(
        performance_result(value, captured_at=same_time, delta=str(value))
        for value in (4, 1, 3, 2)
    )
    batches = {
        (60, Decimal("0.20")): unordered,
        (60, Decimal("0.30")): unordered,
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=4,
        holdout_ratio=Decimal("0.5"),
    ).cohorts[0]
    assert cohort.scenario_results[0].research.mean_return_delta == Decimal("1.5")
    assert cohort.scenario_results[0].holdout.mean_return_delta == Decimal("3.5")


@pytest.mark.parametrize(
    ("count", "ratio", "expected"),
    [
        (3, "0.34", (1, 2)),
        (2, "0.99", (1, 1)),
        (2, "0.01", (1, 1)),
    ],
)
def test_ratio_uses_decimal_ceiling_and_preserves_both_sides(
    count, ratio, expected
) -> None:
    definitions = scenarios()
    batches = {
        (60, Decimal("0.20")): rows(count=count, liquidity="0.20"),
        (60, Decimal("0.30")): rows(count=count, liquidity="0.30"),
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=count,
        holdout_ratio=ratio,
    ).cohorts[0]
    assert (cohort.research_snapshot_count, cohort.holdout_snapshot_count) == expected


def test_baseline_signature_top_n_and_horizon_remain_separate_cohorts() -> None:
    definitions = scenarios()
    batches = {}
    for horizon in (60, 240):
        mixed = (
            *rows(count=2, liquidity="0.20", horizon=horizon),
            *rows(
                count=2,
                liquidity="0.20",
                baseline_signature="baseline-b",
                start=datetime(2026, 2, 1, tzinfo=UTC),
                horizon=horizon,
                first_id=3,
            ),
            *rows(
                count=2,
                liquidity="0.20",
                top_n=5,
                start=datetime(2026, 3, 1, tzinfo=UTC),
                horizon=horizon,
                first_id=5,
            ),
        )
        batches[(horizon, Decimal("0.20"))] = mixed
        batches[(horizon, Decimal("0.30"))] = mixed
    validator, _ = service(batches)
    result = validator.evaluate(
        scenarios=definitions,
        horizons=(60, 240),
        latest=6,
    )
    assert result.cohort_count == 6
    assert {
        (item.horizon_minutes, item.baseline_policy_signature, item.effective_top_n)
        for item in result.cohorts
    } == {
        (60, "baseline-a", 3),
        (60, "baseline-a", 5),
        (60, "baseline-b", 3),
        (240, "baseline-a", 3),
        (240, "baseline-a", 5),
        (240, "baseline-b", 3),
    }


def test_only_all_scenario_success_intersection_is_split() -> None:
    definitions = scenarios()
    batches = {
        (60, Decimal("0.20")): rows(count=4, liquidity="0.20"),
        (60, Decimal("0.30")): rows(
            count=4,
            liquidity="0.30",
            statuses=[SUCCESS, OUTCOME_INCOMPLETE, SUCCESS, SUCCESS],
        ),
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=4,
        holdout_ratio=Decimal("0.30"),
    ).cohorts[0]
    assert cohort.candidate_snapshot_count == 4
    assert cohort.common_comparable_snapshot_count == 3
    assert cohort.common_coverage_rate == Decimal("0.75")
    assert (cohort.research_snapshot_count, cohort.holdout_snapshot_count) == (2, 1)


def test_research_and_holdout_can_reverse_without_automatic_decision() -> None:
    definitions = scenarios()
    batches = {
        (60, Decimal("0.20")): rows(
            count=4,
            liquidity="0.20",
            deltas=["10", "10", "-10", "-10"],
        ),
        (60, Decimal("0.30")): rows(
            count=4,
            liquidity="0.30",
            deltas=["-10", "-10", "10", "10"],
        ),
    }
    validator, _ = service(batches)
    result = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=4,
        holdout_ratio=Decimal("0.5"),
    )
    first, second = result.cohorts[0].scenario_results
    assert first.research.mean_return_delta > 0 > first.holdout.mean_return_delta
    assert second.research.mean_return_delta < 0 < second.holdout.mean_return_delta
    assert result.policy_decision_performed is False
    assert not hasattr(result, "winner")
    assert not hasattr(result, "recommended_scenario")


@pytest.mark.parametrize("count", [0, 1])
def test_too_few_common_snapshots_are_safe_non_comparable_reports(count) -> None:
    definitions = scenarios()
    batches = {
        (60, Decimal("0.20")): rows(count=count, liquidity="0.20"),
        (60, Decimal("0.30")): rows(count=count, liquidity="0.30"),
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=10,
    ).cohorts[0]
    assert cohort.status == (
        NO_COMMON_COMPARABLE_SNAPSHOTS
        if count == 0
        else INSUFFICIENT_TEMPORAL_SPLIT_DATA
    )
    assert cohort.performance_compared is False
    assert cohort.research_snapshot_count == cohort.holdout_snapshot_count == 0
    assert all(
        item.research.mean_return_delta is None
        and item.holdout.mean_return_delta is None
        for item in cohort.scenario_results
    )


@pytest.mark.parametrize("ratio", ["0", "1", "-0.1", "1.1", "NaN", "Infinity", True])
def test_ratio_must_be_finite_and_strictly_between_zero_and_one(ratio) -> None:
    with pytest.raises(HoldoutInputError):
        RankingHoldoutValidationService(MagicMock()).evaluate(
            scenarios=scenarios(),
            horizons=(60,),
            latest=2,
            holdout_ratio=ratio,
        )


def test_fixed_cutoff_includes_boundary_in_research_and_later_in_holdout() -> None:
    definitions = scenarios()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    batches = {
        (60, Decimal("0.20")): rows(count=4, liquidity="0.20", start=start),
        (60, Decimal("0.30")): rows(count=4, liquidity="0.30", start=start),
    }
    validator, _ = service(batches)
    result = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=4,
        research_cutoff_at=start + timedelta(hours=2),
    )
    cohort = result.cohorts[0]
    assert result.split_mode == FIXED_TEMPORAL_CUTOFF
    assert result.holdout_ratio is None
    assert result.strict_unseen_holdout == "not_verified"
    assert (cohort.research_snapshot_count, cohort.holdout_snapshot_count) == (2, 2)
    assert cohort.research_end_at == start + timedelta(hours=2)
    assert cohort.holdout_start_at == start + timedelta(hours=3)


def test_naive_cutoff_and_mutually_exclusive_split_inputs_are_rejected() -> None:
    validator = RankingHoldoutValidationService(MagicMock())
    with pytest.raises(HoldoutInputError, match="timezone-aware"):
        validator.evaluate(
            scenarios=scenarios(),
            horizons=(60,),
            latest=2,
            research_cutoff_at=datetime(2026, 1, 1),
        )
    with pytest.raises(HoldoutInputError, match="mutually exclusive"):
        validator.evaluate(
            scenarios=scenarios(),
            horizons=(60,),
            latest=2,
            holdout_ratio=Decimal("0.3"),
            research_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_fixed_cutoff_with_empty_side_does_not_publish_partial_metrics() -> None:
    definitions = scenarios()
    batches = {
        (60, Decimal("0.20")): rows(count=3, liquidity="0.20"),
        (60, Decimal("0.30")): rows(count=3, liquidity="0.30"),
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=3,
        research_cutoff_at=datetime(2030, 1, 1, tzinfo=UTC),
    ).cohorts[0]
    assert cohort.status == INSUFFICIENT_TEMPORAL_SPLIT_DATA
    assert cohort.performance_compared is False
    assert cohort.research_snapshot_count == cohort.holdout_snapshot_count == 0
    assert all(
        item.research.mean_return_delta is None for item in cohort.scenario_results
    )


def test_sweep_metadata_integrity_failure_is_preserved_fail_closed() -> None:
    definitions = scenarios()
    first = rows(count=2, liquidity="0.20")
    second = list(rows(count=2, liquidity="0.30"))
    second[0].pipeline_run_id = "corrupt"
    batches = {
        (60, Decimal("0.20")): first,
        (60, Decimal("0.30")): tuple(second),
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=2,
    ).cohorts[0]
    assert cohort.status == INVALID_HOLDOUT_DATA
    assert cohort.performance_compared is False
    assert "metadata mismatch" in cohort.safe_reason


def test_naive_snapshot_timestamp_is_invalid_holdout_data() -> None:
    definitions = scenarios()
    naive = tuple(
        performance_result(index, captured_at=datetime(2026, 1, index), delta="1")
        for index in (1, 2)
    )
    batches = {
        (60, Decimal("0.20")): naive,
        (60, Decimal("0.30")): naive,
    }
    validator, _ = service(batches)
    cohort = validator.evaluate(
        scenarios=definitions,
        horizons=(60,),
        latest=2,
    ).cohorts[0]
    assert cohort.status == INVALID_HOLDOUT_DATA
    assert "invalid temporal metadata" in cohort.safe_reason


def test_service_has_no_external_client_write_or_random_split_dependency() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "crypto_trading_bot"
        / "services"
        / "ranking_holdout_validation_service.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "random.",
        "Upbit",
        "OpenAI",
        "Telegram",
        "CoinGecko",
        "self.session.add(",
        "self.session.flush(",
        "self.session.commit(",
    ):
        assert forbidden not in source
