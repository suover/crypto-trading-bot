import argparse
import json
from decimal import Decimal

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    parse_cost_rate,
)
from crypto_trading_bot.services.forward_candidate_cost_adjusted_evidence_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_FORWARD_COST_ADJUSTED_EVIDENCE,
    RESULT_TYPE,
    ForwardCandidateCostAdjustedEvidenceResult,
    ForwardCandidateCostAdjustedEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


REPORT_TYPE = "FORWARD_CANDIDATE_COST_ADJUSTED_EVIDENCE"


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
            "Apply explicit execution-cost assumptions to aligned Forward-only "
            "Candidate Gross and Turnover evidence."
        )
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument(
        "--horizon", type=_positive_integer, action="append", required=True
    )
    parser.add_argument("--fee-rate", type=_rate_argument, required=True)
    parser.add_argument("--spread-cost-rate", type=_rate_argument, required=True)
    parser.add_argument("--slippage-rate", type=_rate_argument, required=True)
    namespace = parser.parse_args(args)
    if len(namespace.horizon) != len(set(namespace.horizon)):
        parser.error("duplicate --horizon values are not allowed")
    return namespace


def _weights(weights: dict[str, Decimal]) -> str:
    return json.dumps(
        {
            name: "0" if value == 0 else format(value.normalize(), "f")
            for name, value in sorted(weights.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _ids(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def report(result: ForwardCandidateCostAdjustedEvidenceResult) -> list[str]:
    candidate = result.candidate
    assumptions = result.assumptions
    lines = [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        f"gross_metric_type={GROSS_PERFORMANCE_METRIC_TYPE}",
        f"cost_adjusted_metric_type={COST_ADJUSTED_METRIC_TYPE}",
        "research_only=true",
        f"database_write={str(result.database_write).lower()}",
        f"external_calls={str(result.external_calls).lower()}",
        f"live_policy_change={str(result.live_policy_change).lower()}",
        "automatic_policy_selection=false",
        f"policy_decision_performed={str(result.policy_decision_performed).lower()}",
        "promotion_performed=false",
        "shadow_policy_created=false",
        (
            "sample_sufficiency_assessed="
            f"{str(result.sample_sufficiency_assessed).lower()}"
        ),
        (
            "statistical_inference_performed="
            f"{str(result.statistical_inference_performed).lower()}"
        ),
        (
            "candidate_registration_verified="
            f"{str(result.candidate_registration_verified).lower()}"
        ),
        f"forward_anchor_enforced={str(result.forward_anchor_enforced).lower()}",
        (
            "pre_registration_snapshots_excluded="
            f"{str(result.pre_registration_snapshots_excluded).lower()}"
        ),
        (
            "pre_registration_transition_excluded="
            f"{str(result.pre_registration_transition_excluded).lower()}"
        ),
        (
            "forward_continuity_enforced="
            f"{str(result.forward_continuity_enforced).lower()}"
        ),
        f"cost_model_reused={str(result.cost_model_reused).lower()}",
        f"fee_rate={assumptions.fee_rate}",
        f"spread_cost_rate={assumptions.spread_cost_rate}",
        f"slippage_rate={assumptions.slippage_rate}",
        f"total_cost_rate={assumptions.total_cost_rate}",
        f"candidate_id={result.candidate_id}",
        f"candidate_schema_version={getattr(candidate, 'candidate_schema_version', None)}",
        f"scenario_name={getattr(candidate, 'scenario_name', None)}",
        (
            "scenario_definition_signature="
            f"{getattr(candidate, 'scenario_definition_signature', None)}"
        ),
        (
            f"component_weights={_weights(candidate.component_weights)}"
            if candidate is not None
            else "component_weights=None"
        ),
        f"user_id={getattr(candidate, 'user_id', None)}",
        f"exchange={getattr(candidate, 'exchange', None)}",
        f"quote_asset={getattr(candidate, 'quote_asset', None)}",
        (
            "baseline_policy_signature="
            f"{getattr(candidate, 'baseline_policy_signature', None)}"
        ),
        f"effective_top_n={getattr(candidate, 'effective_top_n', None)}",
        f"registered_at={getattr(candidate, 'registered_at', None)}",
        (
            "registration_snapshot_id_watermark="
            f"{getattr(candidate, 'registration_snapshot_id_watermark', None)}"
        ),
        (
            "registration_captured_at_watermark="
            f"{getattr(candidate, 'registration_captured_at_watermark', None)}"
        ),
        f"gross_status={result.gross_status}",
        f"turnover_status={result.turnover_status}",
        f"gross_forward_snapshot_count={len(result.gross_forward_snapshot_ids)}",
        f"gross_forward_snapshot_ids={_ids(result.gross_forward_snapshot_ids)}",
        f"turnover_transition_count={result.turnover_transition_count}",
        (f"turnover_current_snapshot_ids={_ids(result.turnover_current_snapshot_ids)}"),
        f"continuity_break_count={result.continuity_break_count}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
    ]
    for horizon in result.horizons:
        lines.extend(
            (
                f"horizon_minutes={horizon.horizon_minutes}",
                (
                    "eligible_forward_snapshot_count="
                    f"{horizon.eligible_forward_snapshot_count}"
                ),
                (
                    "successful_gross_snapshot_count="
                    f"{horizon.successful_gross_snapshot_count}"
                ),
                f"outcome_incomplete_count={horizon.outcome_incomplete_count}",
                f"forward_transition_count={horizon.forward_transition_count}",
                (
                    "cost_adjustable_forward_snapshot_count="
                    f"{horizon.cost_adjustable_forward_snapshot_count}"
                ),
                (
                    "cost_adjustable_forward_snapshot_ids="
                    f"{_ids(horizon.cost_adjustable_forward_snapshot_ids)}"
                ),
                (
                    "cost_adjustable_coverage_rate="
                    f"{horizon.cost_adjustable_coverage_rate}"
                ),
                f"mean_baseline_gross_return={horizon.mean_baseline_gross_return}",
                f"mean_candidate_gross_return={horizon.mean_candidate_gross_return}",
                f"mean_gross_return_delta={horizon.mean_gross_return_delta}",
                (
                    "mean_baseline_replacement_rate="
                    f"{horizon.mean_baseline_replacement_rate}"
                ),
                (
                    "mean_candidate_replacement_rate="
                    f"{horizon.mean_candidate_replacement_rate}"
                ),
                (
                    "mean_baseline_execution_cost_percentage="
                    f"{horizon.mean_baseline_execution_cost_percentage}"
                ),
                (
                    "mean_candidate_execution_cost_percentage="
                    f"{horizon.mean_candidate_execution_cost_percentage}"
                ),
                (
                    "mean_execution_cost_delta_percentage="
                    f"{horizon.mean_execution_cost_delta_percentage}"
                ),
                (
                    "mean_baseline_cost_adjusted_return="
                    f"{horizon.mean_baseline_cost_adjusted_return}"
                ),
                (
                    "mean_candidate_cost_adjusted_return="
                    f"{horizon.mean_candidate_cost_adjusted_return}"
                ),
                (
                    "mean_cost_adjusted_return_delta="
                    f"{horizon.mean_cost_adjusted_return_delta}"
                ),
                (
                    "median_cost_adjusted_return_delta="
                    f"{horizon.median_cost_adjusted_return_delta}"
                ),
                f"cost_adjusted_win_count={horizon.cost_adjusted_win_count}",
                f"cost_adjusted_loss_count={horizon.cost_adjusted_loss_count}",
                f"tie_count={horizon.tie_count}",
                f"cost_adjusted_win_rate={horizon.cost_adjusted_win_rate}",
                f"horizon_status={horizon.status}",
                f"horizon_safe_reason={horizon.safe_reason}",
            )
        )
        for snapshot in horizon.snapshots:
            lines.extend(
                (
                    f"snapshot_id={snapshot.snapshot_id}",
                    f"previous_snapshot_id={snapshot.previous_snapshot_id}",
                    f"captured_at={snapshot.captured_at}",
                    (f"baseline_replacement_rate={snapshot.baseline_replacement_rate}"),
                    (
                        "candidate_replacement_rate="
                        f"{snapshot.candidate_replacement_rate}"
                    ),
                    (
                        "baseline_execution_cost_percentage="
                        f"{snapshot.baseline_execution_cost_percentage}"
                    ),
                    (
                        "candidate_execution_cost_percentage="
                        f"{snapshot.candidate_execution_cost_percentage}"
                    ),
                    f"baseline_gross_return={snapshot.baseline_gross_return}",
                    f"candidate_gross_return={snapshot.candidate_gross_return}",
                    f"gross_return_delta={snapshot.gross_return_delta}",
                    (
                        "baseline_cost_adjusted_return="
                        f"{snapshot.baseline_cost_adjusted_return}"
                    ),
                    (
                        "candidate_cost_adjusted_return="
                        f"{snapshot.candidate_cost_adjusted_return}"
                    ),
                    (
                        "cost_adjusted_return_delta="
                        f"{snapshot.cost_adjusted_return_delta}"
                    ),
                    (
                        "cost_adjusted_scenario_result="
                        f"{snapshot.cost_adjusted_scenario_result}"
                    ),
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = ForwardCandidateCostAdjustedEvidenceService(session).evaluate(
        candidate_id=namespace.candidate_id,
        horizons=namespace.horizon,
        fee_rate=namespace.fee_rate,
        spread_cost_rate=namespace.spread_cost_rate,
        slippage_rate=namespace.slippage_rate,
    )
    return (
        report(result),
        1 if result.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE else 0,
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
        print(f"Forward candidate cost-adjusted evidence rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            "Forward candidate cost-adjusted evidence failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
