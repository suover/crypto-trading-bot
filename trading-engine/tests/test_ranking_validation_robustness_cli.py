from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import evaluate_ranking_validation_robustness as cli
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS,
)
from crypto_trading_bot.services.ranking_validation_robustness_service import (
    INVALID_ROBUSTNESS_DATA,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    INSUFFICIENT_WALK_FORWARD_DATA,
)


def distribution():
    return SimpleNamespace(
        count=2,
        positive_count=1,
        negative_count=1,
        tie_count=0,
        positive_rate=Decimal("0.5"),
        mean_delta=Decimal("0"),
        median_delta=Decimal("0"),
        min_delta=Decimal("-1"),
        max_delta=Decimal("1"),
        delta_range=Decimal("2"),
        delta_stddev=Decimal("1"),
    )


def robustness_result(status="SUCCESS"):
    computed = status == "SUCCESS"
    scenario = SimpleNamespace(
        name="research_example",
        definition_signature="ranking-scenario-definition-v1:abc",
        component_weights={"liquidity": Decimal("0.2")},
    )
    scenario_result = SimpleNamespace(
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        component_weights=scenario.component_weights,
        fold_statistics=SimpleNamespace(
            statistics=distribution(), worst_fold_index=1, best_fold_index=2
        ),
        snapshot_statistics=SimpleNamespace(
            statistics=distribution(), worst_snapshot_id=3, best_snapshot_id=4
        ),
    )
    return SimpleNamespace(
        requested_snapshot_count=10,
        evaluated_snapshot_count=8,
        scenario_count=1,
        horizon_count=1,
        cohort_count=1,
        initial_research_size=2,
        validation_size=2,
        step_size=2,
        policy_decision_performed=False,
        statistical_inference_performed=False,
        sample_sufficiency_assessed=False,
        strict_unseen_validation="not_verified",
        scenarios=(scenario,),
        cohorts=(
            SimpleNamespace(
                horizon_minutes=60,
                baseline_policy_signature="baseline-a",
                effective_top_n=3,
                candidate_snapshot_count=8,
                common_comparable_snapshot_count=6,
                common_coverage_rate=Decimal("0.75"),
                initial_research_size=2,
                validation_size=2,
                step_size=2,
                fold_count=2 if computed else 0,
                validation_snapshot_count=4 if computed else 0,
                unused_tail_snapshot_count=0,
                status=status,
                safe_reason=None if computed else "safe state",
                robustness_computed=computed,
                scenario_results=(scenario_result,) if computed else (),
            ),
        ),
    )


def test_help_and_required_arguments() -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--help"])
    assert error.value.code == 0
    parsed = cli.parse_arguments(
        [
            "--horizon",
            "60",
            "--scenario-file",
            "scenarios.json",
            "--initial-research-size",
            "40",
            "--validation-size",
            "10",
        ]
    )
    assert parsed.latest == 1
    assert parsed.initial_research_size == 40
    assert parsed.validation_size == 10


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--horizon", "60"],
        [
            "--horizon",
            "0",
            "--scenario-file",
            "x",
            "--initial-research-size",
            "1",
            "--validation-size",
            "1",
        ],
        [
            "--horizon",
            "60",
            "--horizon",
            "60",
            "--scenario-file",
            "x",
            "--initial-research-size",
            "1",
            "--validation-size",
            "1",
        ],
        [
            "--latest",
            "0",
            "--horizon",
            "60",
            "--scenario-file",
            "x",
            "--initial-research-size",
            "1",
            "--validation-size",
            "1",
        ],
        [
            "--horizon",
            "60",
            "--scenario-file",
            "x",
            "--initial-research-size",
            "0",
            "--validation-size",
            "1",
        ],
        [
            "--horizon",
            "60",
            "--scenario-file",
            "x",
            "--initial-research-size",
            "1",
            "--validation-size",
            "0",
        ],
    ],
)
def test_invalid_arguments_exit_two(args) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(args)
    assert error.value.code == 2


def test_run_forwards_explicit_input_once(monkeypatch) -> None:
    namespace = cli.parse_arguments(
        [
            "--latest",
            "100",
            "--horizon",
            "60",
            "--horizon",
            "240",
            "--scenario-file",
            "explicit.json",
            "--initial-research-size",
            "40",
            "--validation-size",
            "10",
        ]
    )
    definition = SimpleNamespace(name="scenario")
    loader = MagicMock(return_value=(definition,))
    service = MagicMock()
    service.evaluate.return_value = robustness_result()
    monkeypatch.setattr(cli, "load_scenario_file", loader)
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_validation_robustness_service.RankingValidationRobustnessService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    loader.assert_called_once_with("explicit.json")
    service.evaluate.assert_called_once_with(
        scenarios=(definition,),
        horizons=[60, 240],
        latest=100,
        initial_research_size=40,
        validation_size=10,
    )
    assert exit_code == 0
    assert lines[0] == "report_type=RANKING_VALIDATION_ROBUSTNESS"


def test_success_report_is_deterministic_and_descriptive_only() -> None:
    first = cli.report(robustness_result())
    assert first == cli.report(robustness_result())
    assert first[:16] == [
        "report_type=RANKING_VALIDATION_ROBUSTNESS",
        "result_type=DETERMINISTIC_RANKING_VALIDATION_ROBUSTNESS_RESEARCH",
        "performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "robustness_statistics_type=DESCRIPTIVE_ONLY",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "policy_decision_performed=false",
        "statistical_inference_performed=false",
        "sample_sufficiency_assessed=false",
        "strict_unseen_validation=not_verified",
        "bootstrap_performed=false",
        "confidence_interval_computed=false",
        "hypothesis_test_performed=false",
    ]
    assert "positive_validation_fold_rate=0.5" in first
    assert "validation_fold_delta_stddev=1" in first
    assert "worst_validation_snapshot_id=3" in first
    lowered = "\n".join(first).lower()
    for forbidden in (
        "winner=",
        "best_scenario=",
        "recommended_policy=",
        "promotion_pass=",
        "robustness_pass=",
        "statistically_significant=",
    ):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (INSUFFICIENT_WALK_FORWARD_DATA, 0),
        (NO_COMMON_COMPARABLE_SNAPSHOTS, 0),
        (INVALID_ROBUSTNESS_DATA, 1),
    ],
)
def test_status_exit_codes(monkeypatch, status, expected_exit) -> None:
    namespace = cli.parse_arguments(
        [
            "--horizon",
            "60",
            "--scenario-file",
            "explicit.json",
            "--initial-research-size",
            "2",
            "--validation-size",
            "2",
        ]
    )
    service = MagicMock()
    service.evaluate.return_value = robustness_result(status)
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (object(),))
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_validation_robustness_service.RankingValidationRobustnessService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == expected_exit
    assert f"status={status}" in lines
    assert "robustness_computed=false" in lines
