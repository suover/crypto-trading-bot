import argparse
from decimal import Decimal
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    load_scenario_file,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    INVALID_TURNOVER_DATA,
    RESULT_TYPE,
    RankingSelectionTurnoverSummary,
    TemporalRankingTurnoverResult,
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
        description="Describe temporal Top-N selection replacement from DB-only replay."
    )
    parser.add_argument("--latest", type=_positive_integer, required=True)
    parser.add_argument("--scenario-file", required=True)
    return parser.parse_args(args)


def _canonical_weights(weights: dict[str, Decimal]) -> str:
    return json.dumps(
        {
            name: "0" if value == 0 else format(value.normalize(), "f")
            for name, value in sorted(weights.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _csv(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "None"


def _summary_report(
    prefix: str, summary: RankingSelectionTurnoverSummary
) -> tuple[str, ...]:
    return (
        f"{prefix}_transition_count={summary.transition_count}",
        f"{prefix}_total_entered_count={summary.total_entered_count}",
        f"{prefix}_total_exited_count={summary.total_exited_count}",
        f"{prefix}_mean_replacement_rate={summary.mean_replacement_rate}",
        f"{prefix}_median_replacement_rate={summary.median_replacement_rate}",
        f"{prefix}_min_replacement_rate={summary.min_replacement_rate}",
        f"{prefix}_max_replacement_rate={summary.max_replacement_rate}",
        f"{prefix}_mean_retention_rate={summary.mean_retention_rate}",
        f"{prefix}_median_retention_rate={summary.median_retention_rate}",
        (
            f"{prefix}_zero_replacement_transition_count="
            f"{summary.zero_replacement_transition_count}"
        ),
        (
            f"{prefix}_full_replacement_transition_count="
            f"{summary.full_replacement_transition_count}"
        ),
    )


def report(result: TemporalRankingTurnoverResult) -> list[str]:
    lines = [
        "report_type=TEMPORAL_RANKING_TURNOVER_RESEARCH",
        f"result_type={RESULT_TYPE}",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "outcome_data_required=false",
        "candidate_outcome_data_used=false",
        "horizon_dependent=false",
        "monetary_turnover_computed=false",
        "rebalance_notional_computed=false",
        "transaction_cost_computed=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"replayed_snapshot_count={result.replayed_snapshot_count}",
        f"scenario_count={result.scenario_count}",
        f"cohort_count={result.cohort_count}",
        "chronology_order=CAPTURED_AT_ASC_SNAPSHOT_ID_ASC",
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
                f"baseline_policy_signature={cohort.baseline_policy_signature}",
                f"effective_top_n={cohort.effective_top_n}",
                f"candidate_snapshot_count={cohort.candidate_snapshot_count}",
                (
                    "common_replayable_snapshot_count="
                    f"{cohort.common_replayable_snapshot_count}"
                ),
                f"common_coverage_rate={cohort.common_coverage_rate}",
                f"transition_count={cohort.transition_count}",
                f"continuity_break_count={cohort.continuity_break_count}",
                f"status={cohort.status}",
                f"safe_reason={cohort.safe_reason}",
                f"turnover_compared={str(cohort.turnover_compared).lower()}",
                *_summary_report("baseline", cohort.baseline_summary),
            )
        )
        for transition in cohort.transitions:
            baseline = transition.baseline
            lines.extend(
                (
                    f"transition_index={transition.transition_index}",
                    f"previous_snapshot_id={baseline.previous_snapshot_id}",
                    f"current_snapshot_id={baseline.current_snapshot_id}",
                    f"previous_captured_at={baseline.previous_captured_at}",
                    f"current_captured_at={baseline.current_captured_at}",
                    f"baseline_retained_markets={_csv(baseline.retained_markets)}",
                    f"baseline_entered_markets={_csv(baseline.entered_markets)}",
                    f"baseline_exited_markets={_csv(baseline.exited_markets)}",
                    f"baseline_retained_count={baseline.retained_count}",
                    f"baseline_entered_count={baseline.entered_count}",
                    f"baseline_exited_count={baseline.exited_count}",
                    f"baseline_retention_rate={baseline.retention_rate}",
                    f"baseline_replacement_rate={baseline.replacement_rate}",
                )
            )
            for scenario in transition.scenarios:
                scenario_transition = scenario.transition
                lines.extend(
                    (
                        f"transition_scenario_name={scenario.scenario_name}",
                        f"scenario_retained_markets={_csv(scenario_transition.retained_markets)}",
                        f"scenario_entered_markets={_csv(scenario_transition.entered_markets)}",
                        f"scenario_exited_markets={_csv(scenario_transition.exited_markets)}",
                        f"scenario_retained_count={scenario_transition.retained_count}",
                        f"scenario_entered_count={scenario_transition.entered_count}",
                        f"scenario_exited_count={scenario_transition.exited_count}",
                        f"scenario_retention_rate={scenario_transition.retention_rate}",
                        f"scenario_replacement_rate={scenario_transition.replacement_rate}",
                        (
                            "replacement_rate_delta_vs_baseline="
                            f"{scenario.replacement_rate_delta_vs_baseline}"
                        ),
                    )
                )
        for scenario in cohort.scenario_summaries:
            lines.extend(
                (
                    f"cohort_scenario_name={scenario.scenario_name}",
                    (
                        "cohort_scenario_definition_signature="
                        f"{scenario.scenario_definition_signature}"
                    ),
                    f"cohort_scenario_signature={scenario.scenario_signature}",
                    *_summary_report("scenario", scenario.summary),
                    (
                        "mean_replacement_rate_delta_vs_baseline="
                        f"{scenario.mean_replacement_rate_delta_vs_baseline}"
                    ),
                    (
                        "median_replacement_rate_delta_vs_baseline="
                        f"{scenario.median_replacement_rate_delta_vs_baseline}"
                    ),
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    from crypto_trading_bot.services.temporal_ranking_turnover_service import (
        TemporalRankingTurnoverService,
    )

    scenarios = load_scenario_file(namespace.scenario_file)
    result = TemporalRankingTurnoverService(session).evaluate(
        scenarios=scenarios, latest=namespace.latest
    )
    return report(result), 1 if result.status == INVALID_TURNOVER_DATA else 0


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
        print(f"Temporal ranking turnover rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Temporal ranking turnover failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
