import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_holdout_validation_service import (
    INVALID_HOLDOUT_DATA,
    PERFORMANCE_METRIC_TYPE,
    RESULT_TYPE,
    RankingHoldoutPeriodAggregate,
    RankingHoldoutValidationResult,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    load_scenario_file,
)


def _ratio_argument(value: str) -> Decimal:
    try:
        ratio = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("ratio must be a decimal") from error
    if not ratio.is_finite() or ratio <= 0 or ratio >= 1:
        raise argparse.ArgumentTypeError("ratio must be greater than 0 and less than 1")
    return ratio


def _cutoff_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("cutoff must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("cutoff must include a timezone offset")
    return parsed


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate explicit ranking scenarios over a temporal holdout."
    )
    parser.add_argument("--latest", type=int, default=1)
    parser.add_argument("--horizon", type=int, action="append", required=True)
    parser.add_argument("--scenario-file", required=True)
    split = parser.add_mutually_exclusive_group()
    split.add_argument("--holdout-ratio", type=_ratio_argument)
    split.add_argument("--research-cutoff-at", type=_cutoff_argument)
    namespace = parser.parse_args(args)
    if namespace.latest < 1:
        parser.error("--latest must be >= 1")
    if any(value < 1 for value in namespace.horizon):
        parser.error("--horizon must be >= 1")
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
        (
            f"{prefix}_median_snapshot_return_delta="
            f"{aggregate.median_snapshot_return_delta}"
        ),
        (
            f"{prefix}_mean_baseline_positive_rate="
            f"{aggregate.mean_baseline_positive_rate}"
        ),
        (
            f"{prefix}_mean_scenario_positive_rate="
            f"{aggregate.mean_scenario_positive_rate}"
        ),
    )


def report(result: RankingHoldoutValidationResult) -> list[str]:
    lines = [
        "report_type=RANKING_HOLDOUT_VALIDATION",
        f"result_type={RESULT_TYPE}",
        f"performance_metric_type={PERFORMANCE_METRIC_TYPE}",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"evaluated_snapshot_count={result.evaluated_snapshot_count}",
        f"scenario_count={result.scenario_count}",
        f"horizon_count={result.horizon_count}",
        f"cohort_count={result.cohort_count}",
        f"split_mode={result.split_mode}",
        f"holdout_ratio={result.holdout_ratio}",
        f"research_cutoff_at={result.research_cutoff_at}",
        f"strict_unseen_holdout={result.strict_unseen_holdout}",
        f"policy_decision_performed={str(result.policy_decision_performed).lower()}",
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
                (
                    "common_comparable_snapshot_count="
                    f"{cohort.common_comparable_snapshot_count}"
                ),
                f"common_coverage_rate={cohort.common_coverage_rate}",
                f"cohort_split_mode={cohort.split_mode}",
                f"research_snapshot_count={cohort.research_snapshot_count}",
                f"holdout_snapshot_count={cohort.holdout_snapshot_count}",
                f"research_start_at={cohort.research_start_at}",
                f"research_end_at={cohort.research_end_at}",
                f"holdout_start_at={cohort.holdout_start_at}",
                f"holdout_end_at={cohort.holdout_end_at}",
                f"status={cohort.status}",
                f"safe_reason={cohort.safe_reason}",
                f"performance_compared={str(cohort.performance_compared).lower()}",
            )
        )
        for comparison in cohort.scenario_results:
            lines.extend(
                (
                    f"cohort_scenario_name={comparison.scenario_name}",
                    (
                        "cohort_scenario_definition_signature="
                        f"{comparison.scenario_definition_signature}"
                    ),
                    *_aggregate_report("research", comparison.research),
                    *_aggregate_report("holdout", comparison.holdout),
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    from crypto_trading_bot.services.ranking_holdout_validation_service import (
        RankingHoldoutValidationService,
    )

    scenarios = load_scenario_file(namespace.scenario_file)
    result = RankingHoldoutValidationService(session).evaluate(
        scenarios=scenarios,
        horizons=namespace.horizon,
        latest=namespace.latest,
        holdout_ratio=namespace.holdout_ratio,
        research_cutoff_at=namespace.research_cutoff_at,
    )
    exit_code = (
        1
        if any(cohort.status == INVALID_HOLDOUT_DATA for cohort in result.cohorts)
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
        print(f"Ranking holdout validation rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Ranking holdout validation failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
