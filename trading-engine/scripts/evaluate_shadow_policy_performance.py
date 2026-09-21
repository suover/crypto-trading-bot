import argparse
from decimal import Decimal

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    parse_cost_rate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_performance_service import (
    INVALID_SHADOW_PERFORMANCE_EVIDENCE,
    RESULT_TYPE,
    ShadowPolicyPerformanceResult,
    ShadowPolicyPerformanceService,
)


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
        description="Evaluate read-only performance from stored Shadow selections."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument(
        "--horizon", type=_positive_integer, action="append", required=True
    )
    parser.add_argument("--fee-rate", type=_rate, required=True)
    parser.add_argument("--spread-cost-rate", type=_rate, required=True)
    parser.add_argument("--slippage-rate", type=_rate, required=True)
    namespace = parser.parse_args(args)
    if len(namespace.horizon) != len(set(namespace.horizon)):
        parser.error("duplicate --horizon values are not allowed")
    return namespace


def _bool(value: bool) -> str:
    return str(value).lower()


def _ids(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def report(result: ShadowPolicyPerformanceResult) -> list[str]:
    enrollment = result.enrollment
    turnover = result.turnover
    assumptions = result.cost_assumptions
    lines = [
        f"report_type={RESULT_TYPE}",
        f"candidate_id={result.candidate_id}",
        f"shadow_enrollment_id={getattr(enrollment, 'id', None)}",
        f"scenario_name={getattr(enrollment, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(enrollment, 'scenario_definition_signature', None)}",
        f"baseline_policy_signature={getattr(enrollment, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(enrollment, 'effective_top_n', None)}",
        f"shadow_enrolled_at={getattr(enrollment, 'shadow_enrolled_at', None)}",
        f"shadow_snapshot_id_watermark={getattr(enrollment, 'shadow_snapshot_id_watermark', None)}",
        f"performance_evidence_as_of={result.performance_evidence_as_of}",
        f"shadow_evaluation_snapshot_id_ceiling={result.shadow_evaluation_snapshot_id_ceiling}",
        f"timeline_snapshot_count={result.timeline_evaluation_count}",
        f"timeline_snapshot_ids={_ids(result.timeline_snapshot_ids)}",
        f"candidate_context_snapshot_count={len(result.candidate_context_snapshot_ids)}",
        f"candidate_context_snapshot_ids={_ids(result.candidate_context_snapshot_ids)}",
        f"successful_selection_snapshot_count={result.successful_selection_count}",
        f"successful_selection_snapshot_ids={_ids(result.successful_selection_snapshot_ids)}",
        f"context_mismatch_count={result.context_mismatch_count}",
        f"baseline_integrity_failed_count={result.baseline_integrity_failed_count}",
        f"replay_incompatible_count={result.replay_incompatible_count}",
        f"observation_span_hours={result.observation_span_hours}",
        f"fee_rate={assumptions.fee_rate}",
        f"spread_cost_rate={assumptions.spread_cost_rate}",
        f"slippage_rate={assumptions.slippage_rate}",
        f"total_cost_rate={assumptions.total_cost_rate}",
        f"turnover_status={getattr(turnover, 'status', None)}",
        f"transition_count={getattr(turnover, 'transition_count', 0)}",
        f"continuity_break_count={getattr(turnover, 'continuity_break_count', 0)}",
        f"baseline_mean_replacement_rate={getattr(getattr(turnover, 'baseline_summary', None), 'mean_replacement_rate', None)}",
        f"shadow_mean_replacement_rate={getattr(getattr(turnover, 'shadow_summary', None), 'mean_replacement_rate', None)}",
        f"baseline_median_replacement_rate={getattr(getattr(turnover, 'baseline_summary', None), 'median_replacement_rate', None)}",
        f"shadow_median_replacement_rate={getattr(getattr(turnover, 'shadow_summary', None), 'median_replacement_rate', None)}",
        f"mean_replacement_rate_delta={getattr(turnover, 'mean_replacement_rate_delta_vs_baseline', None)}",
    ]
    for gross in result.gross:
        prefix = f"gross[{gross.horizon_minutes}]"
        lines.extend(
            (
                f"{prefix}.status={gross.status}",
                f"{prefix}.eligible_shadow_snapshot_count={gross.eligible_shadow_snapshot_count}",
                f"{prefix}.successful_comparable_snapshot_count={gross.successful_comparable_snapshot_count}",
                f"{prefix}.outcome_incomplete_count={gross.outcome_incomplete_count}",
                f"{prefix}.invalid_outcome_count={gross.invalid_outcome_count}",
                f"{prefix}.shadow_win_count={gross.shadow_win_count}",
                f"{prefix}.shadow_loss_count={gross.shadow_loss_count}",
                f"{prefix}.tie_count={gross.tie_count}",
                f"{prefix}.shadow_win_rate={gross.shadow_win_rate}",
                f"{prefix}.mean_baseline_return={gross.mean_baseline_return}",
                f"{prefix}.mean_shadow_return={gross.mean_shadow_return}",
                f"{prefix}.mean_return_delta={gross.mean_return_delta}",
                f"{prefix}.median_snapshot_return_delta={gross.median_snapshot_return_delta}",
            )
        )
    for cost in result.cost_adjusted:
        prefix = f"cost[{cost.horizon_minutes}]"
        lines.extend(
            (
                f"{prefix}.status={cost.status}",
                f"{prefix}.cost_adjustable_shadow_snapshot_count={cost.cost_adjustable_shadow_snapshot_count}",
                f"{prefix}.cost_adjustable_coverage_rate={cost.cost_adjustable_coverage_rate}",
                f"{prefix}.mean_baseline_execution_cost_percentage={cost.mean_baseline_execution_cost_percentage}",
                f"{prefix}.mean_shadow_execution_cost_percentage={cost.mean_shadow_execution_cost_percentage}",
                f"{prefix}.mean_baseline_cost_adjusted_return={cost.mean_baseline_cost_adjusted_return}",
                f"{prefix}.mean_shadow_cost_adjusted_return={cost.mean_shadow_cost_adjusted_return}",
                f"{prefix}.mean_cost_adjusted_return_delta={cost.mean_cost_adjusted_return_delta}",
                f"{prefix}.median_cost_adjusted_return_delta={cost.median_cost_adjusted_return_delta}",
                f"{prefix}.cost_adjusted_shadow_win_count={cost.cost_adjusted_shadow_win_count}",
                f"{prefix}.cost_adjusted_shadow_loss_count={cost.cost_adjusted_shadow_loss_count}",
                f"{prefix}.tie_count={cost.tie_count}",
                f"{prefix}.cost_adjusted_shadow_win_rate={cost.cost_adjusted_shadow_win_rate}",
            )
        )
    lines.extend(
        (
            f"stored_shadow_selection_reused={_bool(result.stored_shadow_selection_reused)}",
            f"offline_replay_performed={_bool(result.offline_replay_performed)}",
            f"database_write={_bool(result.database_write)}",
            f"external_calls={_bool(result.external_calls)}",
            f"live_policy_change={_bool(result.live_policy_change)}",
            f"sample_sufficiency_assessed={_bool(result.sample_sufficiency_assessed)}",
            f"statistical_inference_performed={_bool(result.statistical_inference_performed)}",
            f"policy_decision_performed={_bool(result.policy_decision_performed)}",
            f"promotion_performed={_bool(result.promotion_performed)}",
            f"status={result.status}",
            f"safe_reason={result.safe_reason}",
        )
    )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = ShadowPolicyPerformanceService(session).evaluate(
        candidate_id=namespace.candidate_id,
        horizons=namespace.horizon,
        fee_rate=namespace.fee_rate,
        spread_cost_rate=namespace.spread_cost_rate,
        slippage_rate=namespace.slippage_rate,
    )
    session.rollback()
    return (
        report(result),
        1 if result.status == INVALID_SHADOW_PERFORMANCE_EVIDENCE else 0,
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
        print(f"Shadow performance evidence rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Shadow performance evidence failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
