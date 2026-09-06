import argparse
from decimal import Decimal
import json

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_COST_ADJUSTED_DATA,
    RESULT_TYPE,
    CostAdjustedRankingEvaluationResult,
    CostAdjustedRankingScenarioResult,
    parse_cost_rate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    load_scenario_file,
)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _rate_argument(value: str) -> Decimal:
    try:
        return parse_cost_rate(value, field_name="cost rate")
    except ReplayInputError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply explicit normalized execution-cost assumptions to temporal "
            "ranking-selection changes and stored gross A/B outcomes. Cost rates "
            "are Decimal fractions of traded notional, not percentage points."
        )
    )
    parser.add_argument("--latest", type=_positive_integer, required=True)
    parser.add_argument(
        "--horizon", type=_positive_integer, action="append", required=True
    )
    parser.add_argument("--scenario-file", required=True)
    parser.add_argument("--fee-rate", type=_rate_argument, required=True)
    parser.add_argument("--spread-cost-rate", type=_rate_argument, required=True)
    parser.add_argument("--slippage-rate", type=_rate_argument, required=True)
    namespace = parser.parse_args(args)
    if len(namespace.horizon) != len(set(namespace.horizon)):
        parser.error("duplicate --horizon values are not allowed")
    return namespace


def _canonical_weights(weights: dict[str, Decimal]) -> str:
    return json.dumps(
        {
            name: "0" if value == 0 else format(value.normalize(), "f")
            for name, value in sorted(weights.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _scenario_summary_report(
    result: CostAdjustedRankingScenarioResult,
) -> tuple[str, ...]:
    return (
        f"cohort_scenario_name={result.scenario_name}",
        (
            "cohort_scenario_definition_signature="
            f"{result.scenario_definition_signature}"
        ),
        f"cohort_scenario_signature={result.scenario_signature}",
        f"cost_adjusted_snapshot_count={result.cost_adjusted_snapshot_count}",
        f"mean_baseline_gross_return={result.mean_baseline_gross_return}",
        f"mean_scenario_gross_return={result.mean_scenario_gross_return}",
        f"mean_gross_return_delta={result.mean_gross_return_delta}",
        (
            "mean_baseline_gross_traded_notional_ratio="
            f"{result.mean_baseline_gross_traded_notional_ratio}"
        ),
        (
            "mean_scenario_gross_traded_notional_ratio="
            f"{result.mean_scenario_gross_traded_notional_ratio}"
        ),
        (
            "mean_baseline_execution_cost_percentage="
            f"{result.mean_baseline_execution_cost_percentage}"
        ),
        (
            "mean_scenario_execution_cost_percentage="
            f"{result.mean_scenario_execution_cost_percentage}"
        ),
        (
            "mean_execution_cost_delta_percentage="
            f"{result.mean_execution_cost_delta_percentage}"
        ),
        (
            "mean_baseline_cost_adjusted_return="
            f"{result.mean_baseline_cost_adjusted_return}"
        ),
        (
            "mean_scenario_cost_adjusted_return="
            f"{result.mean_scenario_cost_adjusted_return}"
        ),
        (f"mean_cost_adjusted_return_delta={result.mean_cost_adjusted_return_delta}"),
        (
            "median_cost_adjusted_return_delta="
            f"{result.median_cost_adjusted_return_delta}"
        ),
        (f"cost_adjusted_scenario_win_count={result.cost_adjusted_scenario_win_count}"),
        (
            "cost_adjusted_scenario_loss_count="
            f"{result.cost_adjusted_scenario_loss_count}"
        ),
        f"cost_adjusted_tie_count={result.cost_adjusted_tie_count}",
        (f"cost_adjusted_scenario_win_rate={result.cost_adjusted_scenario_win_rate}"),
    )


def report(result: CostAdjustedRankingEvaluationResult) -> list[str]:
    assumptions = result.assumptions
    lines = [
        "report_type=COST_ADJUSTED_RANKING_COUNTERFACTUAL",
        f"result_type={RESULT_TYPE}",
        f"gross_performance_metric_type={GROSS_PERFORMANCE_METRIC_TYPE}",
        f"cost_adjusted_metric_type={COST_ADJUSTED_METRIC_TYPE}",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "normalized_nav=1",
        "weighting_model=EQUAL_WEIGHT",
        "rebalance_model=SELECTION_CHANGE_ONLY",
        "actual_portfolio_used=false",
        "actual_holdings_used=false",
        "actual_orders_used=false",
        "actual_fills_used=false",
        "actual_pnl_computed=false",
        "stored_spread_used=false",
        "cost_assumptions_explicit=true",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        f"fee_rate={assumptions.fee_rate}",
        f"spread_cost_rate={assumptions.spread_cost_rate}",
        f"slippage_rate={assumptions.slippage_rate}",
        f"total_cost_rate={assumptions.total_cost_rate}",
        "cost_rate_unit=FRACTION_OF_TRADED_NOTIONAL",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"evaluated_snapshot_count={result.evaluated_snapshot_count}",
        f"scenario_count={result.scenario_count}",
        f"horizon_count={result.horizon_count}",
        f"cohort_count={result.cohort_count}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
    ]
    for scenario in result.scenarios:
        lines.extend(
            (
                f"scenario_name={scenario.name}",
                f"scenario_definition_signature={scenario.definition_signature}",
                f"component_weights={_canonical_weights(scenario.component_weights)}",
            )
        )
    for cohort_index, cohort in enumerate(result.cohorts, start=1):
        lines.extend(
            (
                f"cohort_index={cohort_index}",
                f"horizon_minutes={cohort.horizon_minutes}",
                f"baseline_policy_signature={cohort.baseline_policy_signature}",
                f"effective_top_n={cohort.effective_top_n}",
                f"candidate_ab_snapshot_count={cohort.candidate_ab_snapshot_count}",
                (
                    "common_comparable_ab_snapshot_count="
                    f"{cohort.common_comparable_ab_snapshot_count}"
                ),
                f"turnover_transition_count={cohort.turnover_transition_count}",
                (
                    "cost_adjustable_snapshot_count="
                    f"{cohort.cost_adjustable_snapshot_count}"
                ),
                (
                    "cost_adjustable_coverage_rate="
                    f"{cohort.cost_adjustable_coverage_rate}"
                ),
                f"status={cohort.status}",
                f"safe_reason={cohort.safe_reason}",
                f"performance_compared={str(cohort.performance_compared).lower()}",
            )
        )
        for scenario in cohort.scenario_results:
            lines.extend(_scenario_summary_report(scenario))
            for snapshot in scenario.snapshots:
                lines.extend(
                    (
                        f"snapshot_id={snapshot.snapshot_id}",
                        f"pipeline_run_id={snapshot.pipeline_run_id}",
                        f"captured_at={snapshot.captured_at}",
                        f"snapshot_horizon_minutes={snapshot.horizon_minutes}",
                        (
                            "baseline_replacement_rate="
                            f"{snapshot.baseline_replacement_rate}"
                        ),
                        (
                            "baseline_gross_traded_notional_ratio="
                            f"{snapshot.baseline_gross_traded_notional_ratio}"
                        ),
                        (
                            "baseline_execution_cost_percentage="
                            f"{snapshot.baseline_execution_cost_percentage}"
                        ),
                        (
                            "scenario_replacement_rate="
                            f"{snapshot.scenario_replacement_rate}"
                        ),
                        (
                            "scenario_gross_traded_notional_ratio="
                            f"{snapshot.scenario_gross_traded_notional_ratio}"
                        ),
                        (
                            "scenario_execution_cost_percentage="
                            f"{snapshot.scenario_execution_cost_percentage}"
                        ),
                        f"baseline_gross_return={snapshot.baseline_gross_return}",
                        f"scenario_gross_return={snapshot.scenario_gross_return}",
                        f"gross_return_delta={snapshot.gross_return_delta}",
                        (
                            "baseline_cost_adjusted_return="
                            f"{snapshot.baseline_cost_adjusted_return}"
                        ),
                        (
                            "scenario_cost_adjusted_return="
                            f"{snapshot.scenario_cost_adjusted_return}"
                        ),
                        (
                            "cost_adjusted_return_delta="
                            f"{snapshot.cost_adjusted_return_delta}"
                        ),
                    )
                )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
        CostAdjustedRankingEvaluationService,
    )

    scenarios = load_scenario_file(namespace.scenario_file)
    result = CostAdjustedRankingEvaluationService(session).evaluate(
        scenarios=scenarios,
        horizons=namespace.horizon,
        latest=namespace.latest,
        fee_rate=namespace.fee_rate,
        spread_cost_rate=namespace.spread_cost_rate,
        slippage_rate=namespace.slippage_rate,
    )
    return report(result), 1 if result.status == INVALID_COST_ADJUSTED_DATA else 0


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            lines, exit_code = run(session, namespace)
            for line in lines:
                print(line)
            return exit_code
    except ReplayInputError as error:
        print(f"Cost-adjusted ranking evaluation rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            "Cost-adjusted ranking evaluation failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
