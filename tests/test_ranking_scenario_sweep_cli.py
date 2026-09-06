from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import evaluate_ranking_scenario_sweep as cli
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    ScenarioDefinitionError,
)


def scenario(name="scenario_a"):
    return SimpleNamespace(
        name=name,
        definition_signature=f"ranking-scenario-definition-v1:{name}",
        component_weights={"liquidity": Decimal("0.2")},
    )


def comparison(name="scenario_a"):
    return SimpleNamespace(
        scenario_name=name,
        scenario_definition_signature=f"ranking-scenario-definition-v1:{name}",
        raw_successful_snapshot_count=2,
        raw_outcome_incomplete_count=0,
        raw_baseline_integrity_failed_count=0,
        raw_replay_incompatible_count=0,
        raw_invalid_outcome_count=0,
        common_snapshot_count=2,
        scenario_win_count=1,
        scenario_loss_count=0,
        tie_count=1,
        scenario_win_rate=Decimal("0.5"),
        mean_baseline_return=Decimal("1"),
        mean_scenario_return=Decimal("2"),
        mean_return_delta=Decimal("1"),
        median_snapshot_return_delta=Decimal("1"),
        mean_baseline_positive_rate=Decimal("0.5"),
        mean_scenario_positive_rate=Decimal("0.75"),
    )


def sweep_result(*, status="SUCCESS"):
    scenario_result = comparison()
    if status != "SUCCESS":
        for field in (
            "scenario_win_rate",
            "mean_baseline_return",
            "mean_scenario_return",
            "mean_return_delta",
            "median_snapshot_return_delta",
            "mean_baseline_positive_rate",
            "mean_scenario_positive_rate",
        ):
            setattr(scenario_result, field, None)
        scenario_result.common_snapshot_count = 0
        scenario_result.scenario_win_count = 0
        scenario_result.scenario_loss_count = 0
        scenario_result.tie_count = 0
    return SimpleNamespace(
        requested_snapshot_count=2,
        evaluated_snapshot_count=2,
        scenario_count=1,
        horizon_count=1,
        cohort_count=1,
        scenarios=(scenario(),),
        cohorts=(
            SimpleNamespace(
                horizon_minutes=240,
                baseline_policy_signature="baseline-a",
                effective_top_n=7,
                candidate_snapshot_count=2,
                common_comparable_snapshot_count=(2 if status == "SUCCESS" else 0),
                common_coverage_rate=(
                    Decimal("1") if status == "SUCCESS" else Decimal("0")
                ),
                status=status,
                safe_reason=None if status == "SUCCESS" else "invalid metadata",
                performance_compared=status == "SUCCESS",
                scenario_results=(scenario_result,),
            ),
        ),
    )


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--help"])
    assert error.value.code == 0


def test_parser_supports_latest_snapshot_and_multiple_explicit_horizons() -> None:
    latest = cli.parse_arguments(
        [
            "--latest",
            "100",
            "--horizon",
            "60",
            "--horizon",
            "240",
            "--scenario-file",
            "scenarios.json",
        ]
    )
    assert latest.latest == 100
    assert latest.horizon == [60, 240]
    snapshot = cli.parse_arguments(
        [
            "--snapshot-id",
            "2",
            "--horizon",
            "30",
            "--scenario-file",
            "scenarios.json",
        ]
    )
    assert snapshot.snapshot_id == 2
    assert snapshot.horizon == [30]


@pytest.mark.parametrize(
    "args",
    [
        ["--horizon", "60"],
        ["--scenario-file", "x.json"],
        ["--snapshot-id", "0", "--horizon", "60", "--scenario-file", "x.json"],
        ["--latest", "0", "--horizon", "60", "--scenario-file", "x.json"],
        [
            "--snapshot-id",
            "1",
            "--latest",
            "2",
            "--horizon",
            "60",
            "--scenario-file",
            "x.json",
        ],
        ["--horizon", "0", "--scenario-file", "x.json"],
        [
            "--horizon",
            "60",
            "--horizon",
            "60",
            "--scenario-file",
            "x.json",
        ],
        ["--grid", "x", "--horizon", "60", "--scenario-file", "x.json"],
    ],
)
def test_invalid_cli_and_unsupported_grid_syntax_exit_two(args) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(args)
    assert error.value.code == 2


def test_run_loads_only_explicit_file_and_forwards_selection(monkeypatch) -> None:
    namespace = cli.parse_arguments(
        [
            "--latest",
            "2",
            "--horizon",
            "240",
            "--scenario-file",
            "explicit.json",
        ]
    )
    definition = scenario()
    service = MagicMock()
    service.evaluate.return_value = sweep_result()
    loader = MagicMock(return_value=(definition,))
    monkeypatch.setattr(cli, "load_scenario_file", loader)
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_scenario_sweep_service.RankingScenarioSweepService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    loader.assert_called_once_with("explicit.json")
    service.evaluate.assert_called_once_with(
        scenarios=(definition,),
        horizons=[240],
        snapshot_id=None,
        latest=2,
    )
    assert exit_code == 0
    assert lines[:7] == [
        "report_type=RANKING_SCENARIO_SWEEP",
        "result_type=LIMITED_EXPLICIT_RANKING_SCENARIO_RESEARCH",
        "performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
    ]


def test_report_is_deterministic_and_contains_no_automatic_winner() -> None:
    first = cli.report(sweep_result())
    second = cli.report(sweep_result())
    assert first == second
    assert "common_comparable_snapshot_count=2" in first
    assert "mean_return_delta=1" in first
    lowered = "\n".join(first).lower()
    assert "best_scenario=" not in lowered
    assert "recommended_scenario=" not in lowered


def test_integrity_failure_reports_and_exits_one(monkeypatch) -> None:
    namespace = cli.parse_arguments(
        ["--horizon", "240", "--scenario-file", "explicit.json"]
    )
    service = MagicMock()
    service.evaluate.return_value = sweep_result(status="INVALID_SWEEP_DATA")
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (scenario(),))
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_scenario_sweep_service.RankingScenarioSweepService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == 1
    assert "status=INVALID_SWEEP_DATA" in lines
    assert "performance_compared=false" in lines


def test_no_common_set_is_a_successful_diagnostic_report() -> None:
    result = sweep_result(status="NO_COMMON_COMPARABLE_SNAPSHOTS")
    lines = cli.report(result)
    assert "status=NO_COMMON_COMPARABLE_SNAPSHOTS" in lines
    assert "performance_compared=false" in lines
    assert "mean_return_delta=None" in lines


def test_multiple_cohorts_are_reported_in_given_order() -> None:
    first = sweep_result().cohorts[0]
    second = SimpleNamespace(
        **{
            **first.__dict__,
            "horizon_minutes": 1440,
            "baseline_policy_signature": "baseline-b",
        }
    )
    result = sweep_result()
    result.cohorts = (first, second)
    result.cohort_count = 2
    lines = cli.report(result)
    assert [line for line in lines if line.startswith("horizon_minutes=")] == [
        "horizon_minutes=240",
        "horizon_minutes=1440",
    ]


def test_main_returns_two_for_invalid_scenario_file(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "load_scenario_file",
        MagicMock(side_effect=ScenarioDefinitionError("invalid explicit scenario")),
    )
    exit_code = cli.main(["--horizon", "60", "--scenario-file", "invalid.json"])
    assert exit_code == 2
    assert "Ranking scenario sweep rejected" in capsys.readouterr().out
