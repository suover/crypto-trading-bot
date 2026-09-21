import argparse
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    INVALID_SWEEP_DATA,
    PERFORMANCE_METRIC_TYPE,
    RESULT_TYPE,
    RankingScenarioSweepResult,
    load_scenario_file,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run explicit ranking scenario research (DB-only/read-only)."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--snapshot-id", type=int)
    selection.add_argument("--latest", type=int)
    parser.add_argument("--horizon", type=int, action="append", required=True)
    parser.add_argument("--scenario-file", required=True)
    namespace = parser.parse_args(args)
    if namespace.snapshot_id is not None and namespace.snapshot_id < 1:
        parser.error("--snapshot-id must be >= 1")
    if namespace.latest is not None and namespace.latest < 1:
        parser.error("--latest must be >= 1")
    if any(value < 1 for value in namespace.horizon):
        parser.error("--horizon must be >= 1")
    if len(namespace.horizon) != len(set(namespace.horizon)):
        parser.error("duplicate --horizon values are not allowed")
    return namespace


def _canonical_weights(weights) -> str:
    return json.dumps(
        {
            name: ("0" if value == 0 else format(value.normalize(), "f"))
            for name, value in sorted(weights.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def report(result: RankingScenarioSweepResult) -> list[str]:
    lines = [
        "report_type=RANKING_SCENARIO_SWEEP",
        f"result_type={RESULT_TYPE}",
        f"performance_metric_type={PERFORMANCE_METRIC_TYPE}",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"evaluated_snapshot_count={result.evaluated_snapshot_count}",
        f"scenario_count={result.scenario_count}",
        f"horizon_count={result.horizon_count}",
        f"cohort_count={result.cohort_count}",
    ]
    for scenario in result.scenarios:
        lines.extend(
            (
                f"scenario_name={scenario.name}",
                (f"scenario_definition_signature={scenario.definition_signature}"),
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
                    (
                        "raw_successful_snapshot_count="
                        f"{comparison.raw_successful_snapshot_count}"
                    ),
                    (
                        "raw_outcome_incomplete_count="
                        f"{comparison.raw_outcome_incomplete_count}"
                    ),
                    (
                        "raw_baseline_integrity_failed_count="
                        f"{comparison.raw_baseline_integrity_failed_count}"
                    ),
                    (
                        "raw_replay_incompatible_count="
                        f"{comparison.raw_replay_incompatible_count}"
                    ),
                    (
                        "raw_invalid_outcome_count="
                        f"{comparison.raw_invalid_outcome_count}"
                    ),
                    f"common_snapshot_count={comparison.common_snapshot_count}",
                    f"scenario_win_count={comparison.scenario_win_count}",
                    f"scenario_loss_count={comparison.scenario_loss_count}",
                    f"tie_count={comparison.tie_count}",
                    f"scenario_win_rate={comparison.scenario_win_rate}",
                    f"mean_baseline_return={comparison.mean_baseline_return}",
                    f"mean_scenario_return={comparison.mean_scenario_return}",
                    f"mean_return_delta={comparison.mean_return_delta}",
                    (
                        "median_snapshot_return_delta="
                        f"{comparison.median_snapshot_return_delta}"
                    ),
                    (
                        "mean_baseline_positive_rate="
                        f"{comparison.mean_baseline_positive_rate}"
                    ),
                    (
                        "mean_scenario_positive_rate="
                        f"{comparison.mean_scenario_positive_rate}"
                    ),
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    from crypto_trading_bot.services.ranking_scenario_sweep_service import (
        RankingScenarioSweepService,
    )

    scenarios = load_scenario_file(namespace.scenario_file)
    result = RankingScenarioSweepService(session).evaluate(
        scenarios=scenarios,
        horizons=namespace.horizon,
        snapshot_id=namespace.snapshot_id,
        latest=namespace.latest,
    )
    exit_code = (
        1
        if any(cohort.status == INVALID_SWEEP_DATA for cohort in result.cohorts)
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
        print(f"Ranking scenario sweep rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Ranking scenario sweep failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
