from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_validation_robustness_service import (
    INVALID_COST_ADJUSTED_ROBUSTNESS_DATA,
    CostAdjustedValidationRobustnessService,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    CostAdjustedWalkForwardValidationService,
)
from scripts import evaluate_cost_adjusted_validation_robustness as cli
from tests.test_cost_adjusted_walk_forward_validation import _cohort, _source


def _result():
    source = _source(_cohort(5, adjusted_deltas=("10", "20", "-2", "0", "4")))
    walk = CostAdjustedWalkForwardValidationService(MagicMock()).evaluate_from_result(
        source, initial_research_size=2, validation_size=1
    )
    return CostAdjustedValidationRobustnessService(MagicMock()).evaluate_from_results(
        source, walk
    )


def test_help_and_required_arguments():
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--help"])
    assert error.value.code == 0
    parsed = cli.parse_arguments(
        [
            "--latest",
            "100",
            "--horizon",
            "60",
            "--scenario-file",
            "scenarios.json",
            "--fee-rate",
            "0.0005",
            "--spread-cost-rate",
            "0.0005",
            "--slippage-rate",
            "0.001",
            "--initial-research-size",
            "40",
            "--validation-size",
            "10",
        ]
    )
    assert parsed.latest == 100
    assert parsed.horizon == [60]
    assert parsed.fee_rate == Decimal("0.0005")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--latest", "0"],
        ["--latest", "1", "--horizon", "0"],
        ["--latest", "1", "--horizon", "60", "--horizon", "60"],
        ["--latest", "1", "--horizon", "60", "--fee-rate", "NaN"],
    ],
)
def test_bad_cli_input_exits_two(arguments):
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(arguments)
    assert error.value.code == 2


def test_run_forwards_explicit_inputs_once(monkeypatch):
    namespace = SimpleNamespace(
        scenario_file="explicit.json",
        horizon=[60, 240],
        latest=100,
        fee_rate=Decimal("0.0005"),
        spread_cost_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
        initial_research_size=40,
        validation_size=10,
    )
    scenarios = (object(),)
    loader = MagicMock(return_value=scenarios)
    service = MagicMock()
    service.evaluate.return_value = _result()
    monkeypatch.setattr(cli, "load_scenario_file", loader)
    monkeypatch.setattr(
        "crypto_trading_bot.services.cost_adjusted_validation_robustness_service.CostAdjustedValidationRobustnessService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    loader.assert_called_once_with("explicit.json")
    service.evaluate.assert_called_once_with(
        scenarios=scenarios,
        horizons=[60, 240],
        latest=100,
        fee_rate=Decimal("0.0005"),
        spread_cost_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
        initial_research_size=40,
        validation_size=10,
    )
    assert exit_code == 0
    assert lines[0] == "report_type=COST_ADJUSTED_RANKING_VALIDATION_ROBUSTNESS"


def test_report_is_deterministic_descriptive_only_and_complete():
    result = _result()
    lines = cli.report(result)
    assert lines == cli.report(result)
    assert lines[:17] == [
        "report_type=COST_ADJUSTED_RANKING_VALIDATION_ROBUSTNESS",
        "result_type=DETERMINISTIC_COST_ADJUSTED_RANKING_VALIDATION_ROBUSTNESS_RESEARCH",
        "gross_performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "cost_adjusted_metric_type=COST_ADJUSTED_RANKING_SELECTION_PERFORMANCE_PROXY",
        "robustness_statistics_type=DESCRIPTIVE_ONLY",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        "statistical_inference_performed=false",
        "sample_sufficiency_assessed=false",
        "strict_unseen_validation=not_verified",
        "bootstrap_performed=false",
        "confidence_interval_computed=false",
        "hypothesis_test_performed=false",
    ]
    assert "fee_rate=0.0005" in lines
    assert "fold_statistics_count=3" in lines
    assert "snapshot_statistics_count=3" in lines
    assert "worst_fold_index=1" in lines
    assert "best_snapshot_id=5" in lines
    lowered = "\n".join(lines).lower()
    for forbidden in (
        "winner=",
        "best_scenario=",
        "promotion_pass=",
        "recommended_policy=",
        "apply_policy=",
    ):
        assert forbidden not in lowered


def test_invalid_result_exits_one(monkeypatch):
    invalid = SimpleNamespace(
        **{
            **vars(_result()),
            "status": INVALID_COST_ADJUSTED_ROBUSTNESS_DATA,
            "safe_reason": "broken lineage",
            "cohorts": (),
            "cohort_count": 0,
        }
    )
    service = MagicMock()
    service.evaluate.return_value = invalid
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (object(),))
    monkeypatch.setattr(
        "crypto_trading_bot.services.cost_adjusted_validation_robustness_service.CostAdjustedValidationRobustnessService",
        lambda _: service,
    )
    namespace = SimpleNamespace(
        scenario_file="x",
        horizon=[60],
        latest=10,
        fee_rate=Decimal("0"),
        spread_cost_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
        initial_research_size=2,
        validation_size=1,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == 1
    assert "status=INVALID_COST_ADJUSTED_ROBUSTNESS_DATA" in lines
