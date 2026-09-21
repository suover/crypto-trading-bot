import argparse
import json
from decimal import Decimal

from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_FORWARD_EVIDENCE,
    RESULT_TYPE,
    ForwardCandidateGrossEvidenceResult,
    ForwardCandidateGrossEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


REPORT_TYPE = "FORWARD_CANDIDATE_GROSS_EVIDENCE"


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
        description=(
            "Evaluate one registered research policy candidate using only "
            "strictly post-registration stored gross evidence."
        )
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument(
        "--horizon", type=_positive_integer, action="append", required=True
    )
    return parser.parse_args(args)


def _weights(weights: dict[str, Decimal]) -> str:
    return json.dumps(
        {
            name: "0" if value == 0 else format(value.normalize(), "f")
            for name, value in sorted(weights.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def report(result: ForwardCandidateGrossEvidenceResult) -> list[str]:
    candidate = result.candidate
    lines = [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        f"performance_metric_type={GROSS_PERFORMANCE_METRIC_TYPE}",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        "promotion_performed=false",
        "shadow_policy_created=false",
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
            "registration_time_provenance_verified="
            f"{str(result.registration_time_provenance_verified).lower()}"
        ),
        (
            "future_snapshot_cutoff_verified="
            f"{str(result.future_snapshot_cutoff_verified).lower()}"
        ),
        (
            "scenario_definition_frozen_at_registration="
            f"{str(result.scenario_definition_frozen_at_registration).lower()}"
        ),
        (
            "forward_evidence_generated="
            f"{str(result.forward_evidence_generated).lower()}"
        ),
        (
            "forward_validation_performed="
            f"{str(result.forward_validation_performed).lower()}"
        ),
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
        (
            "eligible_forward_snapshot_ids="
            + ",".join(str(value) for value in result.eligible_forward_snapshot_ids)
        ),
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
                    "successful_comparable_snapshot_count="
                    f"{horizon.successful_comparable_snapshot_count}"
                ),
                f"outcome_incomplete_count={horizon.outcome_incomplete_count}",
                (
                    "baseline_integrity_failed_count="
                    f"{horizon.baseline_integrity_failed_count}"
                ),
                f"replay_incompatible_count={horizon.replay_incompatible_count}",
                f"invalid_outcome_count={horizon.invalid_outcome_count}",
                f"scenario_win_count={horizon.scenario_win_count}",
                f"scenario_loss_count={horizon.scenario_loss_count}",
                f"tie_count={horizon.tie_count}",
                f"scenario_win_rate={horizon.scenario_win_rate}",
                f"mean_baseline_return={horizon.mean_baseline_return}",
                f"mean_scenario_return={horizon.mean_scenario_return}",
                f"mean_return_delta={horizon.mean_return_delta}",
                (
                    "median_snapshot_return_delta="
                    f"{horizon.median_snapshot_return_delta}"
                ),
                (f"mean_baseline_positive_rate={horizon.mean_baseline_positive_rate}"),
                (f"mean_scenario_positive_rate={horizon.mean_scenario_positive_rate}"),
                f"horizon_status={horizon.status}",
                f"horizon_safe_reason={horizon.safe_reason}",
            )
        )
        for snapshot in horizon.snapshots:
            lines.extend(
                (
                    f"snapshot_id={snapshot.snapshot_id}",
                    f"pipeline_run_id={snapshot.pipeline_run_id}",
                    f"captured_at={snapshot.captured_at}",
                    f"snapshot_horizon_minutes={snapshot.horizon_minutes}",
                    f"snapshot_status={snapshot.status}",
                    f"snapshot_safe_reason={snapshot.safe_reason}",
                    (
                        "performance_evaluated="
                        f"{str(snapshot.performance_evaluated).lower()}"
                    ),
                    (
                        "snapshot_baseline_policy_signature="
                        f"{snapshot.baseline_policy_signature}"
                    ),
                    f"scenario_signature={snapshot.scenario_signature}",
                    f"baseline_top_markets={','.join(snapshot.baseline_top_markets)}",
                    f"scenario_top_markets={','.join(snapshot.scenario_top_markets)}",
                    f"baseline_mean_return={snapshot.baseline_mean_return}",
                    f"scenario_mean_return={snapshot.scenario_mean_return}",
                    f"snapshot_mean_return_delta={snapshot.mean_return_delta}",
                    f"scenario_result={snapshot.scenario_result}",
                )
            )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = ForwardCandidateGrossEvidenceService(session).evaluate(
        candidate_id=namespace.candidate_id,
        horizons=namespace.horizon,
    )
    return report(result), 1 if result.status == INVALID_FORWARD_EVIDENCE else 0


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
        print(f"Forward candidate gross evidence rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            "Forward candidate gross evidence failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
