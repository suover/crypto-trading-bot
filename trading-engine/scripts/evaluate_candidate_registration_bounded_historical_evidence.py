import argparse
import json
from decimal import Decimal

from crypto_trading_bot.services.candidate_registration_bounded_historical_evidence_service import (
    INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE,
    RESULT_TYPE,
    CandidateRegistrationBoundedHistoricalEvidenceResult,
    CandidateRegistrationBoundedHistoricalEvidenceService,
)
from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    parse_cost_rate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


REPORT_TYPE = "CANDIDATE_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE"


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _rate(value: str) -> Decimal:
    try:
        return parse_cost_rate(value, field_name="cost rate")
    except ReplayInputError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstruct candidate Historical evidence as of registration."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument(
        "--horizon", type=_positive_integer, action="append", required=True
    )
    parser.add_argument(
        "--initial-research-size", type=_positive_integer, required=True
    )
    parser.add_argument("--validation-size", type=_positive_integer, required=True)
    parser.add_argument("--fee-rate", type=_rate, required=True)
    parser.add_argument("--spread-cost-rate", type=_rate, required=True)
    parser.add_argument("--slippage-rate", type=_rate, required=True)
    namespace = parser.parse_args(args)
    if len(namespace.horizon) != len(set(namespace.horizon)):
        parser.error("duplicate --horizon values are not allowed")
    return namespace


def _ids(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def _weights(values: dict[str, Decimal]) -> str:
    return json.dumps(
        {name: str(value) for name, value in sorted(values.items())},
        sort_keys=True,
        separators=(",", ":"),
    )


def _statistics(prefix, statistics):
    return [
        f"{prefix}_positive_rate={statistics.positive_rate}",
        f"{prefix}_mean_delta={statistics.mean_delta}",
        f"{prefix}_median_delta={statistics.median_delta}",
        f"{prefix}_min_delta={statistics.min_delta}",
        f"{prefix}_max_delta={statistics.max_delta}",
        f"{prefix}_stddev={statistics.delta_stddev}",
    ]


def report(result: CandidateRegistrationBoundedHistoricalEvidenceResult) -> list[str]:
    candidate = result.candidate
    assumptions = result.assumptions
    lines = [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        "research_only=true",
        f"database_write={str(result.database_write).lower()}",
        f"external_calls={str(result.external_calls).lower()}",
        f"live_policy_change={str(result.live_policy_change).lower()}",
        "automatic_policy_selection=false",
        f"policy_decision_performed={str(result.policy_decision_performed).lower()}",
        "promotion_performed=false",
        "shadow_policy_created=false",
        f"sample_sufficiency_assessed={str(result.sample_sufficiency_assessed).lower()}",
        f"statistical_inference_performed={str(result.statistical_inference_performed).lower()}",
        f"candidate_registration_verified={str(result.candidate_registration_verified).lower()}",
        f"registration_time_evidence_enforced={str(result.registration_time_evidence_enforced).lower()}",
        f"post_registration_snapshots_excluded={str(result.post_registration_snapshots_excluded).lower()}",
        f"post_registration_outcomes_excluded={str(result.post_registration_outcomes_excluded).lower()}",
        f"historical_strict_unseen_validation={result.historical_strict_unseen_validation}",
        f"candidate_id={result.candidate_id}",
        f"candidate_schema_version={getattr(candidate, 'candidate_schema_version', None)}",
        f"scenario_name={getattr(candidate, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(candidate, 'scenario_definition_signature', None)}",
        f"component_weights={_weights(candidate.component_weights) if candidate else None}",
        f"user_id={getattr(candidate, 'user_id', None)}",
        f"exchange={getattr(candidate, 'exchange', None)}",
        f"quote_asset={getattr(candidate, 'quote_asset', None)}",
        f"baseline_policy_signature={getattr(candidate, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(candidate, 'effective_top_n', None)}",
        f"reference_snapshot_id={getattr(candidate, 'reference_snapshot_id', None)}",
        f"registered_at={getattr(candidate, 'registered_at', None)}",
        f"registration_snapshot_id_watermark={result.registration_snapshot_id_watermark}",
        f"registration_captured_at_watermark={result.registration_captured_at_watermark}",
        f"historical_evidence_as_of={result.historical_evidence_as_of}",
        f"historical_timeline_snapshot_count={len(result.historical_timeline_snapshot_ids)}",
        f"historical_timeline_snapshot_ids={_ids(result.historical_timeline_snapshot_ids)}",
        f"historical_candidate_snapshot_count={len(result.historical_candidate_snapshot_ids)}",
        f"historical_candidate_snapshot_ids={_ids(result.historical_candidate_snapshot_ids)}",
        "post_registration_snapshot_count_used=0",
        f"outcome_as_of={result.historical_evidence_as_of}",
        f"fee_rate={assumptions.fee_rate}",
        f"spread_cost_rate={assumptions.spread_cost_rate}",
        f"slippage_rate={assumptions.slippage_rate}",
        f"total_cost_rate={assumptions.total_cost_rate}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
    ]
    if result.turnover is not None:
        target = result.target_turnover_cohort
        baseline_rate = (
            target.baseline_summary.mean_replacement_rate if target else None
        )
        candidate_rate = (
            target.scenario_summaries[0].summary.mean_replacement_rate
            if target and target.scenario_summaries
            else None
        )
        lines.extend(
            (
                f"historical_turnover_status={result.turnover.status}",
                f"historical_turnover_transition_count={getattr(target, 'transition_count', 0)}",
                f"historical_turnover_continuity_break_count={getattr(target, 'continuity_break_count', 0)}",
                f"baseline_mean_replacement_rate={baseline_rate}",
                f"candidate_mean_replacement_rate={candidate_rate}",
                f"replacement_rate_delta={candidate_rate - baseline_rate if candidate_rate is not None and baseline_rate is not None else None}",
            )
        )
    if result.gross_robustness is not None:
        for cohort in result.gross_robustness.cohorts:
            lines.extend(
                (
                    f"horizon_minutes={cohort.horizon_minutes}",
                    f"candidate_snapshot_count={cohort.candidate_snapshot_count}",
                    f"common_comparable_snapshot_count={cohort.common_comparable_snapshot_count}",
                    f"common_coverage_rate={cohort.common_coverage_rate}",
                    f"gross_walk_forward_fold_count={cohort.fold_count}",
                    f"gross_validation_snapshot_count={cohort.validation_snapshot_count}",
                    f"gross_robustness_status={cohort.status}",
                )
            )
            for scenario in cohort.scenario_results:
                lines.extend(
                    _statistics("gross_fold", scenario.fold_statistics.statistics)
                )
                lines.extend(
                    _statistics(
                        "gross_snapshot", scenario.snapshot_statistics.statistics
                    )
                )
    if result.cost_robustness is not None:
        for cohort in result.cost_robustness.cohorts:
            lines.extend(
                (
                    f"cost_adjusted_horizon_minutes={cohort.horizon_minutes}",
                    f"cost_adjusted_candidate_snapshot_count={cohort.candidate_ab_snapshot_count}",
                    f"cost_adjustable_snapshot_count={cohort.cost_adjustable_snapshot_count}",
                    f"cost_adjustable_coverage_rate={cohort.cost_adjustable_coverage_rate}",
                    f"cost_adjusted_walk_forward_fold_count={cohort.fold_count}",
                    f"cost_adjusted_validation_snapshot_count={cohort.validation_snapshot_count}",
                    f"cost_adjusted_robustness_status={cohort.status}",
                )
            )
            for scenario in cohort.scenario_results:
                lines.extend(
                    _statistics("cost_fold", scenario.fold_statistics.statistics)
                )
                lines.extend(
                    _statistics(
                        "cost_snapshot", scenario.snapshot_statistics.statistics
                    )
                )
    return lines


def run(session, namespace):
    result = CandidateRegistrationBoundedHistoricalEvidenceService(session).evaluate(
        candidate_id=namespace.candidate_id,
        horizons=namespace.horizon,
        initial_research_size=namespace.initial_research_size,
        validation_size=namespace.validation_size,
        fee_rate=namespace.fee_rate,
        spread_cost_rate=namespace.spread_cost_rate,
        slippage_rate=namespace.slippage_rate,
    )
    return report(result), (
        1 if result.status == INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE else 0
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
        print(f"Registration-bounded Historical evidence rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            "Registration-bounded Historical evidence failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
