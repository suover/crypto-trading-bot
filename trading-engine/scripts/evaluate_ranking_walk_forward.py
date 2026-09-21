import argparse
from decimal import Decimal
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_holdout_validation_service import (
    RankingHoldoutPeriodAggregate,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    load_scenario_file,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    INVALID_WALK_FORWARD_DATA,
    PERFORMANCE_METRIC_TYPE,
    RESULT_TYPE,
    RankingWalkForwardValidationResult,
)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run expanding-window validation for fixed ranking scenarios."
    )
    parser.add_argument("--latest", type=_positive_integer, default=1)
    parser.add_argument(
        "--horizon", type=_positive_integer, action="append", required=True
    )
    parser.add_argument("--scenario-file", required=True)
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


def _aggregate_report(
    prefix: str, aggregate: RankingHoldoutPeriodAggregate
) -> tuple[str, ...]:
    return (
        f"{prefix}_snapshot_count={aggregate.snapshot_count}",
        f"{prefix}_scenario_win_count={aggregate.scenario_win_count}",
        f"{prefix}_scenario_loss_count={aggregate.scenario_loss_count}",
        f"{prefix}_tie_count={aggregate.tie_count}",
        f"{prefix}_scenario_win_rate={aggregate.scenario_win_rate}",
        f"{prefix}_mean_baseline_return={aggregate.mean_baseline_return}",
        f"{prefix}_mean_scenario_return={aggregate.mean_scenario_return}",
        f"{prefix}_mean_return_delta={aggregate.mean_return_delta}",
        f"{prefix}_median_snapshot_return_delta={aggregate.median_snapshot_return_delta}",
        f"{prefix}_mean_baseline_positive_rate={aggregate.mean_baseline_positive_rate}",
        f"{prefix}_mean_scenario_positive_rate={aggregate.mean_scenario_positive_rate}",
    )


def report(result: RankingWalkForwardValidationResult) -> list[str]:
    lines = [
        "report_type=RANKING_WALK_FORWARD_VALIDATION",
        f"result_type={RESULT_TYPE}",
        f"performance_metric_type={PERFORMANCE_METRIC_TYPE}",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
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
                f"horizon_minutes={cohort.horizon_minutes}",
                f"cohort_index={cohort_index}",
                f"baseline_policy_signature={cohort.baseline_policy_signature}",
                f"effective_top_n={cohort.effective_top_n}",
                f"candidate_snapshot_count={cohort.candidate_snapshot_count}",
                f"common_comparable_snapshot_count={cohort.common_comparable_snapshot_count}",
                f"common_coverage_rate={cohort.common_coverage_rate}",
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
                    f"fold_research_snapshot_count={fold.research_snapshot_count}",
                    f"fold_validation_snapshot_count={fold.validation_snapshot_count}",
                    f"fold_research_start_at={fold.research_start_at}",
                    f"fold_research_end_at={fold.research_end_at}",
                    f"fold_validation_start_at={fold.validation_start_at}",
                    f"fold_validation_end_at={fold.validation_end_at}",
                )
            )
            for comparison in fold.scenario_results:
                lines.extend(
                    (
                        f"fold_scenario_name={comparison.scenario_name}",
                        f"fold_scenario_definition_signature={comparison.scenario_definition_signature}",
                        *_aggregate_report("fold_research", comparison.research),
                        *_aggregate_report("fold_validation", comparison.validation),
                    )
                )
        for summary in cohort.scenario_results:
            lines.extend(
                (
                    f"cohort_scenario_name={summary.scenario_name}",
                    f"cohort_scenario_definition_signature={summary.scenario_definition_signature}",
                    f"cohort_scenario_component_weights={_canonical_weights(summary.component_weights)}",
                    f"validation_fold_count={summary.validation_fold_count}",
                    f"positive_validation_fold_count={summary.positive_validation_fold_count}",
                    f"negative_validation_fold_count={summary.negative_validation_fold_count}",
                    f"tie_validation_fold_count={summary.tie_validation_fold_count}",
                    f"mean_validation_return_delta={summary.mean_validation_return_delta}",
                    f"median_validation_return_delta={summary.median_validation_return_delta}",
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
        RankingWalkForwardValidationService,
    )

    scenarios = load_scenario_file(namespace.scenario_file)
    result = RankingWalkForwardValidationService(session).evaluate(
        scenarios=scenarios,
        horizons=namespace.horizon,
        latest=namespace.latest,
        initial_research_size=namespace.initial_research_size,
        validation_size=namespace.validation_size,
    )
    exit_code = (
        1
        if any(cohort.status == INVALID_WALK_FORWARD_DATA for cohort in result.cohorts)
        else 0
    )
    return report(result), exit_code


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
        print(f"Ranking walk-forward validation rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            f"Ranking walk-forward validation failed. error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
