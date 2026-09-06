import argparse
from decimal import Decimal
import json

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    parse_cost_rate,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
    REPORT_TYPE,
    RESULT_TYPE,
    CostAdjustedWalkForwardValidationResult,
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
            "Build expanding, non-overlapping validation folds from one "
            "cost-adjusted ranking evaluation."
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
    parser.add_argument(
        "--initial-research-size", type=_positive_integer, required=True
    )
    parser.add_argument("--validation-size", type=_positive_integer, required=True)
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


def report(result: CostAdjustedWalkForwardValidationResult) -> list[str]:
    assumptions = result.assumptions
    lines = [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        f"gross_performance_metric_type={GROSS_PERFORMANCE_METRIC_TYPE}",
        f"cost_adjusted_metric_type={COST_ADJUSTED_METRIC_TYPE}",
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
        f"policy_decision_performed={str(result.policy_decision_performed).lower()}",
        f"strict_unseen_validation={result.strict_unseen_validation}",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"evaluated_snapshot_count={result.evaluated_snapshot_count}",
        f"scenario_count={result.scenario_count}",
        f"horizon_count={result.horizon_count}",
        f"cohort_count={result.cohort_count}",
        f"initial_research_size={result.initial_research_size}",
        f"validation_size={result.validation_size}",
        f"step_size={result.step_size}",
        f"fee_rate={assumptions.fee_rate}",
        f"spread_cost_rate={assumptions.spread_cost_rate}",
        f"slippage_rate={assumptions.slippage_rate}",
        f"total_cost_rate={assumptions.total_cost_rate}",
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
                (
                    "cost_adjustable_snapshot_count="
                    f"{cohort.cost_adjustable_snapshot_count}"
                ),
                (
                    "cost_adjustable_coverage_rate="
                    f"{cohort.cost_adjustable_coverage_rate}"
                ),
                f"cohort_initial_research_size={cohort.initial_research_size}",
                f"cohort_validation_size={cohort.validation_size}",
                f"cohort_step_size={cohort.step_size}",
                f"fold_count={cohort.fold_count}",
                f"unused_tail_snapshot_count={cohort.unused_tail_snapshot_count}",
                f"status={cohort.status}",
                f"safe_reason={cohort.safe_reason}",
                f"performance_compared={str(cohort.performance_compared).lower()}",
            )
        )
        for fold in cohort.folds:
            lines.extend(
                (
                    f"fold_index={fold.fold_index}",
                    f"research_snapshot_count={fold.research_snapshot_count}",
                    f"validation_snapshot_count={fold.validation_snapshot_count}",
                    f"research_start_at={fold.research_start_at}",
                    f"research_end_at={fold.research_end_at}",
                    f"validation_start_at={fold.validation_start_at}",
                    f"validation_end_at={fold.validation_end_at}",
                    f"research_snapshot_ids={','.join(map(str, fold.research_snapshot_ids))}",
                    (
                        "validation_snapshot_ids="
                        f"{','.join(map(str, fold.validation_snapshot_ids))}"
                    ),
                )
            )
            for scenario in fold.scenario_results:
                lines.extend(
                    (
                        f"fold_scenario_name={scenario.scenario_name}",
                        (
                            "fold_scenario_definition_signature="
                            f"{scenario.scenario_definition_signature}"
                        ),
                        f"fold_scenario_signature={scenario.scenario_signature}",
                    )
                )
                for period_name, period in (
                    ("research", scenario.research),
                    ("validation", scenario.validation),
                ):
                    prefix = f"fold_{period_name}"
                    lines.extend(
                        (
                            f"{prefix}_mean_gross_return_delta={period.mean_gross_return_delta}",
                            (
                                f"{prefix}_median_gross_return_delta="
                                f"{period.median_gross_return_delta}"
                            ),
                            (
                                f"{prefix}_mean_cost_adjusted_return_delta="
                                f"{period.mean_cost_adjusted_return_delta}"
                            ),
                            (
                                f"{prefix}_median_cost_adjusted_return_delta="
                                f"{period.median_cost_adjusted_return_delta}"
                            ),
                            (
                                f"{prefix}_positive_cost_adjusted_snapshot_count="
                                f"{period.positive_cost_adjusted_snapshot_count}"
                            ),
                            (
                                f"{prefix}_negative_cost_adjusted_snapshot_count="
                                f"{period.negative_cost_adjusted_snapshot_count}"
                            ),
                            (
                                f"{prefix}_tie_cost_adjusted_snapshot_count="
                                f"{period.tie_cost_adjusted_snapshot_count}"
                            ),
                            (
                                f"{prefix}_positive_cost_adjusted_snapshot_rate="
                                f"{period.positive_cost_adjusted_snapshot_rate}"
                            ),
                        )
                    )
        for scenario in cohort.scenario_results:
            lines.extend(
                (
                    f"cohort_scenario_name={scenario.scenario_name}",
                    (
                        "cohort_scenario_definition_signature="
                        f"{scenario.scenario_definition_signature}"
                    ),
                    f"cohort_scenario_signature={scenario.scenario_signature}",
                    f"validation_fold_count={scenario.validation_fold_count}",
                    (
                        "positive_validation_fold_count="
                        f"{scenario.positive_validation_fold_count}"
                    ),
                    (
                        "negative_validation_fold_count="
                        f"{scenario.negative_validation_fold_count}"
                    ),
                    f"tie_validation_fold_count={scenario.tie_validation_fold_count}",
                    (
                        "mean_validation_gross_return_delta="
                        f"{scenario.mean_validation_gross_return_delta}"
                    ),
                    (
                        "median_validation_gross_return_delta="
                        f"{scenario.median_validation_gross_return_delta}"
                    ),
                    (
                        "mean_validation_cost_adjusted_return_delta="
                        f"{scenario.mean_validation_cost_adjusted_return_delta}"
                    ),
                    (
                        "median_validation_cost_adjusted_return_delta="
                        f"{scenario.median_validation_cost_adjusted_return_delta}"
                    ),
                    f"validation_snapshot_count={scenario.validation_snapshot_count}",
                    (
                        "positive_validation_snapshot_count="
                        f"{scenario.positive_validation_snapshot_count}"
                    ),
                    (
                        "negative_validation_snapshot_count="
                        f"{scenario.negative_validation_snapshot_count}"
                    ),
                    (
                        "tie_validation_snapshot_count="
                        f"{scenario.tie_validation_snapshot_count}"
                    ),
                    (
                        "positive_validation_snapshot_rate="
                        f"{scenario.positive_validation_snapshot_rate}"
                    ),
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
        CostAdjustedWalkForwardValidationService,
    )

    scenarios = load_scenario_file(namespace.scenario_file)
    result = CostAdjustedWalkForwardValidationService(session).evaluate(
        scenarios=scenarios,
        horizons=namespace.horizon,
        latest=namespace.latest,
        fee_rate=namespace.fee_rate,
        spread_cost_rate=namespace.spread_cost_rate,
        slippage_rate=namespace.slippage_rate,
        initial_research_size=namespace.initial_research_size,
        validation_size=namespace.validation_size,
    )
    return (
        report(result),
        1 if result.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA else 0,
    )


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
        print(f"Cost-adjusted walk-forward validation rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            "Cost-adjusted walk-forward validation failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
