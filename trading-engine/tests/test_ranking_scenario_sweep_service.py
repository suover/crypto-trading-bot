from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    INVALID_SWEEP_DATA,
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioSweepService,
    parse_scenario_document,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    INVALID_OUTCOME_DATA,
    OUTCOME_INCOMPLETE,
    REPLAY_INCOMPATIBLE,
    StrategyABPerformanceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


def scenarios(count=3):
    raw = []
    for index in range(count):
        liquidity = Decimal("0.20") + Decimal(index) * Decimal("0.01")
        momentum = Decimal("0.30") - Decimal(index) * Decimal("0.01")
        raw.append(
            {
                "name": f"scenario_{index + 1}",
                "component_weights": {
                    "liquidity": str(liquidity),
                    "trend_alignment": "0.20",
                    "momentum": str(momentum),
                    "volume_confirmation": "0.10",
                    "spread": "0.08",
                    "volatility": "0.07",
                    "drawdown": "0.05",
                },
            }
        )
    return parse_scenario_document(
        {"schema_version": "ranking-scenario-sweep-v1", "scenarios": raw}
    )


def performance_result(
    snapshot_id,
    *,
    delta="1",
    status=SUCCESS,
    baseline_signature="baseline-a",
    top_n=7,
    horizon=60,
    pipeline_run_id=None,
    captured_at=None,
    baseline_top_markets=None,
):
    baseline_mean = Decimal(snapshot_id)
    parsed_delta = Decimal(delta)
    evaluated = status == SUCCESS
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        pipeline_run_id=pipeline_run_id or f"pipeline-{snapshot_id}",
        captured_at=captured_at
        or datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=snapshot_id),
        horizon_minutes=horizon,
        baseline_policy_signature=baseline_signature,
        effective_top_n=top_n,
        baseline_top_markets=baseline_top_markets
        or tuple(f"KRW-{index}" for index in range(top_n)),
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
        positive_rate_delta=Decimal("0.1") if evaluated else None,
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
        self.summary_service = StrategyABPerformanceService(MagicMock())

    def evaluate_latest(self, limit, *, horizon_minutes, overrides):
        key = (horizon_minutes, overrides["liquidity"])
        self.calls.append(("latest", limit, key))
        return SimpleNamespace(results=self.batches[key])

    def evaluate_snapshot(self, snapshot_id, *, horizon_minutes, overrides):
        key = (horizon_minutes, overrides["liquidity"])
        self.calls.append(("snapshot", snapshot_id, key))
        return self.batches[key][0]

    def summarize_results(self, requested_count, results):
        return self.summary_service.summarize_results(requested_count, results)

    def evaluate_snapshots(
        self, snapshot_ids, *, horizon_minutes, overrides, outcome_as_of=None
    ):
        key = (horizon_minutes, overrides["liquidity"])
        self.calls.append(("snapshots", tuple(snapshot_ids), key, outcome_as_of))
        return self.batches[key]


def test_explicit_matrix_preserves_ids_and_forwards_outcome_as_of() -> None:
    definition = scenarios(1)
    ids = (3, 5, 8, 10)
    as_of = datetime(2026, 9, 2, tzinfo=UTC)
    batches = {
        (60, Decimal("0.20")): tuple(
            performance_result(snapshot_id) for snapshot_id in ids
        )
    }
    performance = FakePerformanceService(batches)
    matrix = RankingScenarioSweepService(
        MagicMock(), performance_service=performance
    ).evaluate_matrix_snapshots(
        scenarios=definition,
        horizons=(60,),
        snapshot_ids=ids,
        outcome_as_of=as_of,
    )

    assert matrix.requested_snapshot_count == matrix.evaluated_snapshot_count == 4
    assert matrix.cohorts[0].candidate_snapshot_ids == ids
    assert performance.calls == [("snapshots", ids, (60, Decimal("0.20")), as_of)]


def test_explicit_matrix_rejects_duplicate_or_reordered_results() -> None:
    definition = scenarios(1)
    performance = FakePerformanceService(
        {(60, Decimal("0.20")): (performance_result(2), performance_result(1))}
    )
    with pytest.raises(ReplayInputError, match="order does not match"):
        RankingScenarioSweepService(
            MagicMock(), performance_service=performance
        ).evaluate_matrix_snapshots(
            scenarios=definition, horizons=(60,), snapshot_ids=(1, 2)
        )


def test_common_set_intersection_drives_every_scenario_aggregate() -> None:
    definitions = scenarios()
    values = (
        ("0.20", ("1", "100", "1000", "10000"), (SUCCESS,) * 4),
        (
            "0.21",
            ("2", "4", "8", "16"),
            (SUCCESS, SUCCESS, SUCCESS, OUTCOME_INCOMPLETE),
        ),
        (
            "0.22",
            ("-1", "0", "-8", "32"),
            (SUCCESS, SUCCESS, OUTCOME_INCOMPLETE, SUCCESS),
        ),
    )
    batches = {
        (60, Decimal(liquidity)): tuple(
            performance_result(index, delta=delta, status=status)
            for index, (delta, status) in enumerate(zip(deltas, statuses), start=1)
        )
        for liquidity, deltas, statuses in values
    }
    service = FakePerformanceService(batches)
    result = RankingScenarioSweepService(
        MagicMock(), performance_service=service
    ).evaluate(scenarios=definitions, horizons=(60,), latest=4)
    cohort = result.cohorts[0]
    assert cohort.candidate_snapshot_count == 4
    assert cohort.common_comparable_snapshot_count == 2
    assert cohort.common_coverage_rate == Decimal("0.5")
    assert [item.raw_successful_snapshot_count for item in cohort.scenario_results] == [
        4,
        3,
        3,
    ]
    assert [item.common_snapshot_count for item in cohort.scenario_results] == [2, 2, 2]
    assert cohort.scenario_results[0].mean_return_delta == Decimal("50.5")
    assert cohort.scenario_results[1].mean_return_delta == Decimal("3")
    assert cohort.scenario_results[2].scenario_loss_count == 1
    assert cohort.scenario_results[2].tie_count == 1
    assert len(service.calls) == 3


def test_no_common_snapshot_reports_diagnostics_without_comparison_metrics() -> None:
    definitions = scenarios(2)
    batches = {
        (60, Decimal("0.20")): (
            performance_result(1),
            performance_result(2, status=OUTCOME_INCOMPLETE),
        ),
        (60, Decimal("0.21")): (
            performance_result(1, status=OUTCOME_INCOMPLETE),
            performance_result(2),
        ),
    }
    cohort = (
        RankingScenarioSweepService(
            MagicMock(), performance_service=FakePerformanceService(batches)
        )
        .evaluate(scenarios=definitions, horizons=(60,), latest=2)
        .cohorts[0]
    )
    assert cohort.status == NO_COMMON_COMPARABLE_SNAPSHOTS
    assert cohort.performance_compared is False
    assert cohort.common_comparable_snapshot_count == 0
    assert all(item.mean_return_delta is None for item in cohort.scenario_results)
    assert [item.raw_successful_snapshot_count for item in cohort.scenario_results] == [
        1,
        1,
    ]


@pytest.mark.parametrize(
    ("excluded_status", "count_field"),
    [
        (OUTCOME_INCOMPLETE, "raw_outcome_incomplete_count"),
        (BASELINE_INTEGRITY_FAILED, "raw_baseline_integrity_failed_count"),
        (REPLAY_INCOMPATIBLE, "raw_replay_incompatible_count"),
        (INVALID_OUTCOME_DATA, "raw_invalid_outcome_count"),
    ],
)
def test_each_non_success_status_is_excluded_and_reported(
    excluded_status, count_field
) -> None:
    definitions = scenarios(2)
    batches = {
        (60, Decimal("0.20")): (
            performance_result(1),
            performance_result(2, status=excluded_status),
        ),
        (60, Decimal("0.21")): (
            performance_result(1),
            performance_result(2),
        ),
    }
    cohort = (
        RankingScenarioSweepService(
            MagicMock(), performance_service=FakePerformanceService(batches)
        )
        .evaluate(scenarios=definitions, horizons=(60,), latest=2)
        .cohorts[0]
    )
    assert cohort.common_comparable_snapshot_count == 1
    assert getattr(cohort.scenario_results[0], count_field) == 1
    assert all(result.common_snapshot_count == 1 for result in cohort.scenario_results)


def test_empty_dataset_reports_no_common_cohort_per_horizon() -> None:
    definitions = scenarios(1)
    batches = {
        (60, Decimal("0.20")): (),
        (240, Decimal("0.20")): (),
    }
    result = RankingScenarioSweepService(
        MagicMock(), performance_service=FakePerformanceService(batches)
    ).evaluate(scenarios=definitions, horizons=(60, 240), latest=10)
    assert result.evaluated_snapshot_count == 0
    assert result.cohort_count == 2
    assert all(
        cohort.status == NO_COMMON_COMPARABLE_SNAPSHOTS
        and cohort.candidate_snapshot_count == 0
        and cohort.common_comparable_snapshot_count == 0
        for cohort in result.cohorts
    )


def test_baseline_signature_and_effective_top_n_create_separate_cohorts() -> None:
    definitions = scenarios(2)
    rows = (
        performance_result(1, baseline_signature="baseline-a", top_n=7),
        performance_result(2, baseline_signature="baseline-a", top_n=7),
        performance_result(3, baseline_signature="baseline-b", top_n=7),
        performance_result(4, baseline_signature="baseline-b", top_n=10),
    )
    batches = {
        (60, Decimal("0.20")): rows,
        (60, Decimal("0.21")): rows,
    }
    result = RankingScenarioSweepService(
        MagicMock(), performance_service=FakePerformanceService(batches)
    ).evaluate(scenarios=definitions, horizons=(60,), latest=4)
    assert result.cohort_count == 3
    assert {
        (
            item.baseline_policy_signature,
            item.effective_top_n,
            item.candidate_snapshot_count,
        )
        for item in result.cohorts
    } == {
        ("baseline-a", 7, 2),
        ("baseline-b", 7, 1),
        ("baseline-b", 10, 1),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_policy_signature", "different"),
        ("effective_top_n", 10),
        ("pipeline_run_id", "different"),
        ("captured_at", datetime(2030, 1, 1, tzinfo=UTC)),
        ("baseline_top_markets", ("KRW-X",)),
    ],
)
def test_scenario_metadata_mismatch_fails_closed(field, value) -> None:
    definitions = scenarios(2)
    first = performance_result(1)
    second = performance_result(1)
    setattr(second, field, value)
    batches = {
        (60, Decimal("0.20")): (first,),
        (60, Decimal("0.21")): (second,),
    }
    cohort = (
        RankingScenarioSweepService(
            MagicMock(), performance_service=FakePerformanceService(batches)
        )
        .evaluate(scenarios=definitions, horizons=(60,), latest=1)
        .cohorts[0]
    )
    assert cohort.status == INVALID_SWEEP_DATA
    assert cohort.performance_compared is False
    assert cohort.common_comparable_snapshot_count == 0
    assert all(item.mean_return_delta is None for item in cohort.scenario_results)


def test_baseline_metric_mismatch_on_common_snapshot_fails_closed() -> None:
    definitions = scenarios(2)
    first = performance_result(1)
    second = performance_result(1)
    second.baseline_mean_return = Decimal("999")
    batches = {
        (60, Decimal("0.20")): (first,),
        (60, Decimal("0.21")): (second,),
    }
    cohort = (
        RankingScenarioSweepService(
            MagicMock(), performance_service=FakePerformanceService(batches)
        )
        .evaluate(scenarios=definitions, horizons=(60,), latest=1)
        .cohorts[0]
    )
    assert cohort.status == INVALID_SWEEP_DATA
    assert "baseline metrics mismatch" in cohort.safe_reason


def test_horizons_have_independent_common_sets_and_no_composite_result() -> None:
    definitions = scenarios(2)
    batches = {
        (60, Decimal("0.20")): (
            performance_result(1, horizon=60),
            performance_result(2, status=OUTCOME_INCOMPLETE, horizon=60),
        ),
        (60, Decimal("0.21")): (
            performance_result(1, horizon=60),
            performance_result(2, horizon=60),
        ),
        (240, Decimal("0.20")): (
            performance_result(1, status=OUTCOME_INCOMPLETE, horizon=240),
            performance_result(2, horizon=240),
        ),
        (240, Decimal("0.21")): (
            performance_result(1, horizon=240),
            performance_result(2, horizon=240),
        ),
    }
    result = RankingScenarioSweepService(
        MagicMock(), performance_service=FakePerformanceService(batches)
    ).evaluate(scenarios=definitions, horizons=(60, 240), latest=2)
    assert result.horizon_count == 2
    assert [item.horizon_minutes for item in result.cohorts] == [60, 240]
    assert [item.common_comparable_snapshot_count for item in result.cohorts] == [1, 1]
    assert not hasattr(result, "overall_score")
    assert not hasattr(result, "best_scenario")


def test_single_snapshot_and_file_order_are_preserved() -> None:
    definitions = scenarios(2)
    batches = {
        (30, Decimal("0.20")): (performance_result(5, horizon=30),),
        (30, Decimal("0.21")): (performance_result(5, horizon=30),),
    }
    service = FakePerformanceService(batches)
    result = RankingScenarioSweepService(
        MagicMock(), performance_service=service
    ).evaluate(scenarios=definitions, horizons=(30,), snapshot_id=5)
    assert result.requested_snapshot_count == result.evaluated_snapshot_count == 1
    assert [item.scenario_name for item in result.cohorts[0].scenario_results] == [
        "scenario_1",
        "scenario_2",
    ]
    assert all(call[0] == "snapshot" for call in service.calls)


@pytest.mark.parametrize("horizons", [(), (0,), (-1,), (True,), (60, 60)])
def test_invalid_or_duplicate_horizons_are_rejected(horizons) -> None:
    with pytest.raises(Exception):
        RankingScenarioSweepService(
            MagicMock(), performance_service=MagicMock()
        ).evaluate(scenarios=scenarios(1), horizons=horizons, latest=1)


@pytest.mark.parametrize("latest", [0, -1, True, Decimal("1")])
def test_invalid_latest_selection_is_rejected(latest) -> None:
    with pytest.raises(Exception, match="latest snapshot count must be >= 1"):
        RankingScenarioSweepService(
            MagicMock(), performance_service=MagicMock()
        ).evaluate(scenarios=scenarios(1), horizons=(60,), latest=latest)


def test_service_contains_no_optimizer_external_client_or_database_write() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "crypto_trading_bot"
        / "services"
        / "ranking_scenario_sweep_service.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "itertools.product",
        "permutations",
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
