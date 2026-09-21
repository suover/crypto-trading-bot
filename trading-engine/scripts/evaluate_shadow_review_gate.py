import argparse
from dataclasses import fields

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_review_gate_service import (
    INVALID_REVIEW_DATA,
    RESULT_TYPE,
    ShadowReviewGateResult,
    ShadowReviewGateService,
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
        description="Apply the read-only Shadow Review Gate v1 policy."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    return parser.parse_args(args)


def _bool(value: bool) -> str:
    return str(value).lower()


def report(result: ShadowReviewGateResult) -> list[str]:
    enrollment = result.enrollment
    performance = result.performance
    lines = [
        f"report_type={RESULT_TYPE}",
        f"candidate_id={result.candidate_id}",
        f"shadow_enrollment_id={getattr(enrollment, 'id', None)}",
        f"scenario_name={getattr(enrollment, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(enrollment, 'scenario_definition_signature', None)}",
        f"baseline_policy_signature={getattr(enrollment, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(enrollment, 'effective_top_n', None)}",
        f"shadow_enrolled_at={getattr(enrollment, 'shadow_enrolled_at', None)}",
        f"review_policy_schema_version={result.review_policy_schema_version}",
        f"review_policy_signature={result.review_policy_signature}",
    ]
    for field in fields(result.review_policy):
        value = getattr(result.review_policy, field.name)
        if isinstance(value, tuple):
            value = ",".join(str(item) for item in value)
        lines.append(f"{field.name}={value}")
    lines.extend(
        (
            f"stored_pre_shadow_gate_policy_signature={getattr(enrollment, 'gate_policy_signature', None)}",
            f"stored_pre_shadow_gate_decision_signature={getattr(enrollment, 'gate_decision_signature', None)}",
            f"pre_shadow_gate_provenance_verified={_bool(result.pre_shadow_gate_provenance_verified)}",
            f"performance_evidence_as_of={getattr(performance, 'performance_evidence_as_of', None)}",
            f"shadow_evaluation_snapshot_id_ceiling={getattr(performance, 'shadow_evaluation_snapshot_id_ceiling', None)}",
            f"shadow_performance_provenance_verified={_bool(result.shadow_performance_provenance_verified)}",
            f"passed_check_count={len(result.passed_checks)}",
            f"insufficient_check_count={len(result.insufficient_checks)}",
            f"failed_check_count={len(result.failed_checks)}",
            f"invalid_check_count={len(result.invalid_checks)}",
        )
    )
    for index, check in enumerate(result.all_checks, start=1):
        prefix = f"check[{index}]"
        lines.extend(
            (
                f"{prefix}.check_id={check.check_id}",
                f"{prefix}.category={check.category}",
                f"{prefix}.status={check.status}",
                f"{prefix}.horizon={check.horizon_minutes}",
                f"{prefix}.observed={check.observed_value}",
                f"{prefix}.comparator={check.comparator}",
                f"{prefix}.threshold={check.threshold_value}",
                f"{prefix}.reason={check.reason}",
            )
        )
    lines.extend(
        (
            f"status={result.status}",
            f"eligible_for_promotion_review={_bool(result.eligible_for_promotion_review)}",
            f"safe_reason={result.safe_reason}",
            f"review_decision_signature={result.review_decision_signature}",
            f"sample_sufficiency_assessed={_bool(result.sample_sufficiency_assessed)}",
            f"statistical_inference_performed={_bool(result.statistical_inference_performed)}",
            f"policy_decision_performed={_bool(result.policy_decision_performed)}",
            f"promotion_performed={_bool(result.promotion_performed)}",
            f"database_write={_bool(result.database_write)}",
            f"external_calls={_bool(result.external_calls)}",
            f"live_policy_change={_bool(result.live_policy_change)}",
            f"shadow_runtime_changed={_bool(result.shadow_runtime_changed)}",
        )
    )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = ShadowReviewGateService(session).evaluate(
        candidate_id=namespace.candidate_id
    )
    session.rollback()
    return report(result), 1 if result.status == INVALID_REVIEW_DATA else 0


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
        print(f"Shadow review rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Shadow review failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
