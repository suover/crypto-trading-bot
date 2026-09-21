from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA,
    INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
    NO_COST_ADJUSTABLE_SNAPSHOTS,
)
from scripts import evaluate_cost_adjusted_walk_forward as cli


def _period(delta: str):
    value = Decimal(delta)
    return SimpleNamespace(
        mean_gross_return_delta=value + Decimal("0.1"),
        median_gross_return_delta=value + Decimal("0.1"),
        mean_cost_adjusted_return_delta=value,
        median_cost_adjusted_return_delta=value,
        positive_cost_adjusted_snapshot_count=2 if value > 0 else 0,
        negative_cost_adjusted_snapshot_count=2 if value < 0 else 0,
        tie_cost_adjusted_snapshot_count=2 if value == 0 else 0,
        positive_cost_adjusted_snapshot_rate=(
            Decimal("1") if value > 0 else Decimal("0")
        ),
    )


def _result(status="SUCCESS"):
    scenario = SimpleNamespace(
        name="explicit",
        definition_signature="definition-a",
        component_weights={"liquidity": Decimal("1")},
    )
    fold_scenario = SimpleNamespace(
        scenario_name="explicit",
        scenario_definition_signature="definition-a",
        scenario_signature="scenario-a",
        research=_period("1"),
        validation=_period("-1"),
    )
    fold = SimpleNamespace(
        fold_index=1,
        research_snapshot_count=2,
        validation_snapshot_count=2,
        research_start_at=datetime(2026, 1, 1, tzinfo=UTC),
        research_end_at=datetime(2026, 1, 2, tzinfo=UTC),
        validation_start_at=datetime(2026, 1, 3, tzinfo=UTC),
        validation_end_at=datetime(2026, 1, 4, tzinfo=UTC),
        research_snapshot_ids=(2, 3),
        validation_snapshot_ids=(4, 5),
        scenario_results=(fold_scenario,),
    )
    summary = SimpleNamespace(
        scenario_name="explicit",
        scenario_definition_signature="definition-a",
        scenario_signature="scenario-a",
        validation_fold_count=1,
        positive_validation_fold_count=0,
        negative_validation_fold_count=1,
        tie_validation_fold_count=0,
        mean_validation_gross_return_delta=Decimal("-0.9"),
        median_validation_gross_return_delta=Decimal("-0.9"),
        mean_validation_cost_adjusted_return_delta=Decimal("-1"),
        median_validation_cost_adjusted_return_delta=Decimal("-1"),
        validation_snapshot_count=2,
        positive_validation_snapshot_count=0,
        negative_validation_snapshot_count=2,
        tie_validation_snapshot_count=0,
        positive_validation_snapshot_rate=Decimal("0"),
    )
    compared = status == "SUCCESS"
    return SimpleNamespace(
        requested_snapshot_count=100,
        evaluated_snapshot_count=80,
        scenario_count=1,
        horizon_count=1,
        cohort_count=1,
        initial_research_size=2,
        validation_size=2,
        step_size=2,
        assumptions=SimpleNamespace(
            fee_rate=Decimal("0.0005"),
            spread_cost_rate=Decimal("0.0005"),
            slippage_rate=Decimal("0.001"),
            total_cost_rate=Decimal("0.002"),
        ),
        status=status,
        safe_reason=None if compared else "safe reason",
        policy_decision_performed=False,
        strict_unseen_validation="not_verified",
        scenarios=(scenario,),
        cohorts=(
            SimpleNamespace(
                horizon_minutes=60,
                baseline_policy_signature="baseline-a",
                effective_top_n=7,
                candidate_ab_snapshot_count=9,
                common_comparable_ab_snapshot_count=8,
                cost_adjustable_snapshot_count=7,
                cost_adjustable_coverage_rate=Decimal("0.875"),
                initial_research_size=2,
                validation_size=2,
                step_size=2,
                fold_count=1 if compared else 0,
                unused_tail_snapshot_count=1,
                status=status,
                safe_reason=None if compared else "safe reason",
                performance_compared=compared,
                folds=(fold,) if compared else (),
                scenario_results=(summary,),
            ),
        ),
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
    assert parsed.initial_research_size == 40
    assert parsed.validation_size == 10


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
    definition = object()
    loader = MagicMock(return_value=(definition,))
    service = MagicMock()
    service.evaluate.return_value = _result()
    monkeypatch.setattr(cli, "load_scenario_file", loader)
    monkeypatch.setattr(
        "crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service.CostAdjustedWalkForwardValidationService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    loader.assert_called_once_with("explicit.json")
    service.evaluate.assert_called_once_with(
        scenarios=(definition,),
        horizons=[60, 240],
        latest=100,
        fee_rate=Decimal("0.0005"),
        spread_cost_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
        initial_research_size=40,
        validation_size=10,
    )
    assert exit_code == 0
    assert lines[0] == "report_type=COST_ADJUSTED_RANKING_WALK_FORWARD"


def test_report_is_deterministic_safe_and_compares_same_fold_metrics():
    lines = cli.report(_result())
    assert lines == cli.report(_result())
    assert lines[:18] == [
        "report_type=COST_ADJUSTED_RANKING_WALK_FORWARD",
        "result_type=EXPANDING_WINDOW_COST_ADJUSTED_RANKING_VALIDATION_RESEARCH",
        "gross_performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "cost_adjusted_metric_type=COST_ADJUSTED_RANKING_SELECTION_PERFORMANCE_PROXY",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "walk_forward_type=EXPANDING_WINDOW",
        "validation_overlap=false",
        "normalized_nav=1",
        "weighting_model=EQUAL_WEIGHT",
        "rebalance_model=SELECTION_CHANGE_ONLY",
        "actual_portfolio_used=false",
        "actual_pnl_computed=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        "strict_unseen_validation=not_verified",
    ]
    assert "fold_validation_mean_gross_return_delta=-0.9" in lines
    assert "fold_validation_mean_cost_adjusted_return_delta=-1" in lines
    assert "negative_validation_fold_count=1" in lines
    lowered = "\n".join(lines).lower()
    for forbidden in (
        "winner=",
        "best_scenario=",
        "recommended_policy=",
        "promotion_pass=",
        "apply_policy=",
    ):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (NO_COST_ADJUSTABLE_SNAPSHOTS, 0),
        (INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA, 0),
        (INVALID_COST_ADJUSTED_WALK_FORWARD_DATA, 1),
    ],
)
def test_status_exit_codes(monkeypatch, status, exit_code):
    namespace = SimpleNamespace(
        scenario_file="x",
        horizon=[60],
        latest=10,
        fee_rate=Decimal("0"),
        spread_cost_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
        initial_research_size=2,
        validation_size=2,
    )
    service = MagicMock()
    service.evaluate.return_value = _result(status)
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (object(),))
    monkeypatch.setattr(
        "crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service.CostAdjustedWalkForwardValidationService",
        lambda _: service,
    )
    lines, actual = cli.run(MagicMock(), namespace)
    assert actual == exit_code
    assert f"status={status}" in lines
