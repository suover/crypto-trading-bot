import argparse
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_evaluation_service import (
    INVALID_SHADOW_EVALUATION,
    SUCCESS,
    ShadowPolicyEvaluationResult,
    ShadowPolicyEvaluationService,
)


REPORT_TYPE = "SHADOW_SELECTION_EVALUATION"


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
        description="Evaluate post-enrollment Shadow selection evidence from frozen DB data."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(args)


def _bool(value: bool) -> str:
    return str(value).lower()


def _json(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def report(result: ShadowPolicyEvaluationResult) -> list[str]:
    enrollment = result.enrollment
    lines = [
        f"report_type={REPORT_TYPE}",
        f"evaluation_schema_version={result.evaluation_schema_version}",
        f"shadow_runtime_enabled={_bool(result.shadow_runtime_enabled)}",
        f"outcome_data_used={_bool(result.outcome_data_used)}",
        f"performance_evaluated={_bool(result.performance_evaluated)}",
        f"policy_decision_performed={_bool(result.policy_decision_performed)}",
        f"promotion_performed={_bool(result.promotion_performed)}",
        f"database_write={_bool(result.database_write)}",
        f"external_calls={_bool(result.external_calls)}",
        f"live_policy_change={_bool(result.live_policy_change)}",
        f"shadow_enrollment_id={getattr(enrollment, 'id', None)}",
        f"candidate_id={result.candidate_id}",
        f"scenario_name={getattr(enrollment, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(enrollment, 'scenario_definition_signature', None)}",
        f"baseline_policy_signature={getattr(enrollment, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(enrollment, 'effective_top_n', None)}",
        f"gate_decision_signature={getattr(enrollment, 'gate_decision_signature', None)}",
        f"shadow_enrolled_at={getattr(enrollment, 'shadow_enrolled_at', None)}",
        f"shadow_snapshot_id_watermark={getattr(enrollment, 'shadow_snapshot_id_watermark', None)}",
        f"shadow_captured_at_watermark={getattr(enrollment, 'shadow_captured_at_watermark', None)}",
        f"evaluation_snapshot_id_ceiling={result.evaluation_snapshot_id_ceiling}",
        f"timeline_snapshot_count={len(result.timeline_snapshot_ids)}",
        f"timeline_snapshot_ids={_json(result.timeline_snapshot_ids)}",
        f"candidate_context_snapshot_count={len(result.candidate_context_snapshot_ids)}",
        f"candidate_context_snapshot_ids={_json(result.candidate_context_snapshot_ids)}",
        f"existing_evaluation_count={result.existing_evaluation_count}",
        f"planned_evaluation_count={result.planned_evaluation_count}",
        f"created_evaluation_count={result.created_evaluation_count}",
        f"success_count={result.success_count}",
        f"context_mismatch_count={result.context_mismatch_count}",
        f"baseline_integrity_failed_count={result.baseline_integrity_failed_count}",
        f"replay_incompatible_count={result.replay_incompatible_count}",
    ]
    for index, row in enumerate(result.evaluations, start=1):
        prefix = f"evaluation[{index}]"
        lines.extend(
            [
                f"{prefix}.strategy_replay_snapshot_id={row.strategy_replay_snapshot_id}",
                f"{prefix}.pipeline_run_id={row.pipeline_run_id}",
                f"{prefix}.snapshot_captured_at={row.snapshot_captured_at}",
                f"{prefix}.context_matches_enrollment={_bool(row.context_matches_enrollment)}",
                f"{prefix}.replay_status={row.replay_status}",
                f"{prefix}.evaluation_status={row.evaluation_status}",
                f"{prefix}.safe_reason={row.safe_reason}",
                f"{prefix}.scenario_signature={row.scenario_signature}",
                f"{prefix}.baseline_top_markets={_json(row.baseline_top_markets)}",
                f"{prefix}.shadow_top_markets={_json(row.shadow_top_markets)}",
                f"{prefix}.top_n_overlap_count={row.top_n_overlap_count}",
                f"{prefix}.top_n_overlap_rate={row.top_n_overlap_rate}",
                f"{prefix}.entered_top_n={_json(row.entered_top_n)}",
                f"{prefix}.exited_top_n={_json(row.exited_top_n)}",
                f"{prefix}.evaluation_signature={row.evaluation_signature}",
            ]
        )
    lines.extend([f"status={result.status}", f"safe_reason={result.safe_reason}"])
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = ShadowPolicyEvaluationService(session)
    result = (
        service.evaluate(candidate_id=namespace.candidate_id)
        if namespace.apply
        else service.preview(candidate_id=namespace.candidate_id)
    )
    if namespace.apply and result.status == SUCCESS:
        session.commit()
    else:
        session.rollback()
    return report(result), 1 if result.status == INVALID_SHADOW_EVALUATION else 0


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
        print(f"Shadow selection evaluation rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Shadow selection evaluation failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
