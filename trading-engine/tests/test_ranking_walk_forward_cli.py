from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import evaluate_ranking_walk_forward as cli
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    INSUFFICIENT_WALK_FORWARD_DATA,
    INVALID_WALK_FORWARD_DATA,
)


def aggregate(delta="1"):
    value = Decimal(delta)
    return SimpleNamespace(
        snapshot_count=2,
        scenario_win_count=2 if value > 0 else 0,
        scenario_loss_count=2 if value < 0 else 0,
        tie_count=2 if value == 0 else 0,
        scenario_win_rate=Decimal("1") if value > 0 else Decimal("0"),
        mean_baseline_return=Decimal("2"),
        mean_scenario_return=Decimal("2") + value,
        mean_return_delta=value,
        median_snapshot_return_delta=value,
        mean_baseline_positive_rate=Decimal("0.5"),
        mean_scenario_positive_rate=Decimal("0.75"),
    )


def validation_result(status="SUCCESS"):
    compared = status == "SUCCESS"
    scenario = SimpleNamespace(
        name="research_example",
        definition_signature="ranking-scenario-definition-v1:abc",
        component_weights={"liquidity": Decimal("0.2")},
    )
    fold = SimpleNamespace(
        fold_index=1,
        research_snapshot_count=2,
        validation_snapshot_count=2,
        research_start_at=datetime(2026, 1, 1, tzinfo=UTC),
        research_end_at=datetime(2026, 1, 2, tzinfo=UTC),
        validation_start_at=datetime(2026, 1, 3, tzinfo=UTC),
        validation_end_at=datetime(2026, 1, 4, tzinfo=UTC),
        scenario_results=(
            SimpleNamespace(
                scenario_name=scenario.name,
                scenario_definition_signature=scenario.definition_signature,
                research=aggregate("1"),
                validation=aggregate("-1"),
            ),
        ),
    )
    summary = SimpleNamespace(
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        component_weights=scenario.component_weights,
        validation_fold_count=1,
        positive_validation_fold_count=0,
        negative_validation_fold_count=1,
        tie_validation_fold_count=0,
        mean_validation_return_delta=Decimal("-1"),
        median_validation_return_delta=Decimal("-1"),
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
        strict_unseen_validation="not_verified",
        scenarios=(scenario,),
        cohorts=(
            SimpleNamespace(
                horizon_minutes=240,
                baseline_policy_signature="baseline-a",
                effective_top_n=5,
                candidate_snapshot_count=8,
                common_comparable_snapshot_count=4,
                common_coverage_rate=Decimal("0.5"),
                initial_research_size=2,
                validation_size=2,
                step_size=2,
                fold_count=1 if compared else 0,
                unused_tail_snapshot_count=0,
                status=status,
                safe_reason=None if compared else "not comparable",
                performance_compared=compared,
                folds=(fold,) if compared else (),
                scenario_results=(summary,),
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
def test_invalid_cli_arguments_exit_two(args) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(args)
    assert error.value.code == 2


def test_run_forwards_explicit_configuration(monkeypatch) -> None:
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
    service.evaluate.return_value = validation_result()
    monkeypatch.setattr(cli, "load_scenario_file", loader)
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_walk_forward_validation_service.RankingWalkForwardValidationService",
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
    assert lines[0] == "report_type=RANKING_WALK_FORWARD_VALIDATION"


def test_success_report_is_deterministic_safe_and_has_no_decision_fields() -> None:
    first = cli.report(validation_result())
    assert first == cli.report(validation_result())
    assert first[:10] == [
        "report_type=RANKING_WALK_FORWARD_VALIDATION",
        "result_type=EXPANDING_WINDOW_RANKING_WALK_FORWARD_RESEARCH",
        "performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "policy_decision_performed=false",
        "strict_unseen_validation=not_verified",
    ]
    assert "fold_research_mean_return_delta=1" in first
    assert "fold_validation_mean_return_delta=-1" in first
    assert "negative_validation_fold_count=1" in first
    lowered = "\n".join(first).lower()
    for forbidden in (
        "best_scenario=",
        "winner=",
        "recommended_scenario=",
        "recommended_policy=",
        "promotion_pass=",
        "promotion_candidate=",
        "apply_to_live=",
    ):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (INSUFFICIENT_WALK_FORWARD_DATA, 0),
        (NO_COMMON_COMPARABLE_SNAPSHOTS, 0),
        (INVALID_WALK_FORWARD_DATA, 1),
    ],
)
def test_safe_and_integrity_status_exit_codes(
    monkeypatch, status, expected_exit
) -> None:
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
    service.evaluate.return_value = validation_result(status)
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (object(),))
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_walk_forward_validation_service.RankingWalkForwardValidationService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == expected_exit
    assert f"status={status}" in lines
    assert "performance_compared=false" in lines
