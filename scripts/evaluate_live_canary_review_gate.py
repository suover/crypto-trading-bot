import argparse
from dataclasses import fields

from crypto_trading_bot.services.live_canary_review_gate_service import (
    INVALID_CANARY_DATA,
    RESULT_TYPE,
    LiveCanaryReviewGateError,
    LiveCanaryReviewGateResult,
    LiveCanaryReviewGateService,
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
        description="Apply the read-only LIVE Canary Review Gate v1 policy."
    )
    parser.add_argument("--canary-activation-id", type=_positive_integer, required=True)
    return parser.parse_args(args)


def _bool(value: bool) -> str:
    return str(value).lower()


def report(result: LiveCanaryReviewGateResult) -> list[str]:
    lines = [
        f"result_type={RESULT_TYPE}",
        f"canary_activation_id={result.canary_activation_id}",
        f"candidate_id={result.candidate_id}",
        f"evidence_schema_version={result.evidence_schema_version}",
        f"evidence_signature={result.evidence_signature}",
        f"evidence_as_of={result.evidence_as_of}",
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
            f"evaluated_at={result.evaluated_at}",
            f"status={result.status}",
            f"eligible_for_full_live_review={_bool(result.eligible_for_full_live_review)}",
            f"safe_reason={result.safe_reason}",
            f"review_decision_signature={result.review_decision_signature}",
            f"evidence_provenance_verified={_bool(result.evidence_provenance_verified)}",
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
                f"{prefix}.observed={check.observed_value}",
                f"{prefix}.comparator={check.comparator}",
                f"{prefix}.threshold={check.threshold_value}",
                f"{prefix}.reason={check.reason}",
            )
        )
    lines.extend(
        (
            f"sample_sufficiency_assessed={_bool(result.sample_sufficiency_assessed)}",
            f"statistical_inference_performed={_bool(result.statistical_inference_performed)}",
            f"policy_decision_performed={_bool(result.policy_decision_performed)}",
            f"promotion_performed={_bool(result.promotion_performed)}",
            f"full_live_promotion_performed={_bool(result.full_live_promotion_performed)}",
            f"database_write={_bool(result.database_write)}",
            f"external_calls={_bool(result.external_calls)}",
            f"live_policy_change={_bool(result.live_policy_change)}",
            f"live_order_change={_bool(result.live_order_change)}",
            f"canary_state_changed={_bool(result.canary_state_changed)}",
            f"ranking_runtime_changed={_bool(result.ranking_runtime_changed)}",
        )
    )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = LiveCanaryReviewGateService(session).evaluate(
        canary_activation_id=namespace.canary_activation_id
    )
    session.rollback()
    return report(result), 1 if result.status == INVALID_CANARY_DATA else 0


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            lines, exit_code = run(session, namespace)
            for line in lines:
                print(line)
            return exit_code
    except LiveCanaryReviewGateError as error:
        print(f"LIVE Canary review rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"LIVE Canary review failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
