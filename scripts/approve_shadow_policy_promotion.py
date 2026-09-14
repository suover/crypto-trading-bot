import argparse

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    APPROVAL_SCHEMA_VERSION,
    CREATED,
    INVALID_PROMOTION_APPROVAL,
    REPORT_TYPE,
    ShadowPolicyPromotionApprovalResult,
    ShadowPolicyPromotionApprovalService,
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
        description="Preview or record an exact human-approved Shadow decision."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-review-decision-signature")
    namespace = parser.parse_args(args)
    if namespace.apply and namespace.expected_review_decision_signature is None:
        parser.error("--apply requires --expected-review-decision-signature")
    if not namespace.apply and namespace.expected_review_decision_signature is not None:
        parser.error("--expected-review-decision-signature is only valid with --apply")
    return namespace


def _bool(value: bool) -> str:
    return str(value).lower()


def report(result: ShadowPolicyPromotionApprovalResult) -> list[str]:
    approval = result.approval
    review = result.review
    enrollment = getattr(review, "enrollment", None) if review is not None else approval
    performance = getattr(review, "performance", None)
    return [
        f"report_type={REPORT_TYPE}",
        f"candidate_id={result.candidate_id}",
        f"shadow_enrollment_id={getattr(approval, 'shadow_enrollment_id', getattr(enrollment, 'id', None))}",
        f"promotion_approval_id={getattr(approval, 'id', None)}",
        f"scenario_name={getattr(enrollment, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(enrollment, 'scenario_definition_signature', None)}",
        f"baseline_policy_signature={getattr(enrollment, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(enrollment, 'effective_top_n', None)}",
        f"review_status={getattr(review, 'status', getattr(approval, 'review_status', None))}",
        f"review_policy_schema_version={getattr(review, 'review_policy_schema_version', getattr(approval, 'review_policy_schema_version', None))}",
        f"review_policy_signature={getattr(review, 'review_policy_signature', getattr(approval, 'review_policy_signature', None))}",
        f"review_evaluated_at={getattr(review, 'evaluated_at', getattr(approval, 'review_evaluated_at', None))}",
        f"performance_evidence_as_of={getattr(performance, 'performance_evidence_as_of', getattr(approval, 'performance_evidence_as_of', None))}",
        f"shadow_evaluation_snapshot_id_ceiling={getattr(performance, 'shadow_evaluation_snapshot_id_ceiling', getattr(approval, 'shadow_evaluation_snapshot_id_ceiling', None))}",
        f"current_review_decision_signature={result.current_review_decision_signature}",
        f"expected_review_decision_signature={result.expected_review_decision_signature}",
        f"review_signature_matched={_bool(result.review_signature_matched)}",
        f"approval_schema_version={APPROVAL_SCHEMA_VERSION}",
        f"approval_status={result.approval_status}",
        f"approval_source={getattr(approval, 'approval_source', None)}",
        f"human_approved_at={getattr(approval, 'human_approved_at', None)}",
        f"approval_signature={getattr(approval, 'approval_signature', None)}",
        f"human_approval_recorded={_bool(result.human_approval_recorded)}",
        f"promotion_approval_created={_bool(result.promotion_approval_created)}",
        f"promotion_approval_persisted={_bool(result.promotion_approval_persisted)}",
        f"database_write={_bool(result.database_write)}",
        f"external_calls={_bool(result.external_calls)}",
        f"live_policy_change={_bool(result.live_policy_change)}",
        f"live_order_change={_bool(result.live_order_change)}",
        f"ranking_runtime_changed={_bool(result.ranking_runtime_changed)}",
        f"shadow_runtime_changed={_bool(result.shadow_runtime_changed)}",
        f"canary_started={_bool(result.canary_started)}",
        f"safe_reason={result.safe_reason}",
    ]


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = ShadowPolicyPromotionApprovalService(session)
    result = (
        service.approve(
            candidate_id=namespace.candidate_id,
            expected_review_decision_signature=(
                namespace.expected_review_decision_signature
            ),
        )
        if namespace.apply
        else service.preview(candidate_id=namespace.candidate_id)
    )
    if result.approval_status == CREATED:
        session.commit()
    else:
        session.rollback()
    return report(
        result
    ), 1 if result.approval_status == INVALID_PROMOTION_APPROVAL else 0


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
        print(f"Promotion approval rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Promotion approval failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
