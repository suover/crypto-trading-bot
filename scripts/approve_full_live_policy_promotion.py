import argparse
import sys

from crypto_trading_bot.services.full_live_policy_promotion_approval_service import (
    APPROVAL_SCHEMA_VERSION,
    INVALID_FULL_LIVE_PROMOTION_APPROVAL,
    REPORT_TYPE,
    FullLivePolicyPromotionApprovalResult,
    FullLivePolicyPromotionApprovalWorkflow,
    FullLivePromotionReviewArtifact,
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
        description="Preview or record an exact Human-approved Full LIVE Promotion."
    )
    parser.add_argument("--canary-activation-id", type=_positive_integer, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(args)


def _bool(value: bool) -> str:
    return str(value).lower()


def artifact_summary(artifact: FullLivePromotionReviewArtifact) -> list[str]:
    statuses = [item["status"] for item in artifact.review_checks]
    return [
        f"review_status={artifact.review_status}",
        f"passed_check_count={statuses.count('PASS')}",
        f"insufficient_check_count={statuses.count('INSUFFICIENT')}",
        f"failed_check_count={statuses.count('FAIL')}",
        f"invalid_check_count={statuses.count('INVALID')}",
        f"evidence_signature={artifact.evidence_signature}",
        f"review_policy_signature={artifact.review_policy_signature}",
        f"review_decision_signature={artifact.review_decision_signature}",
    ]


def report(result: FullLivePolicyPromotionApprovalResult) -> list[str]:
    approval = result.approval
    artifact = result.artifact
    review = result.review
    evidence_as_of = (
        getattr(artifact, "evidence_as_of", None)
        or getattr(approval, "evidence_as_of", None)
        or getattr(review, "evidence_as_of", None)
    )
    review_evaluated_at = (
        getattr(artifact, "review_evaluated_at", None)
        or getattr(approval, "review_evaluated_at", None)
        or getattr(review, "evaluated_at", None)
    )
    return [
        f"report_type={REPORT_TYPE}",
        f"approval_schema_version={APPROVAL_SCHEMA_VERSION}",
        f"canary_activation_id={result.canary_activation_id}",
        f"candidate_id={result.candidate_id}",
        f"review_result_type={getattr(artifact, 'review_result_type', getattr(approval, 'review_result_type', getattr(review, 'result_type', None)))}",
        f"review_status={result.review_status}",
        f"evidence_schema_version={getattr(artifact, 'evidence_schema_version', getattr(approval, 'evidence_schema_version', getattr(review, 'evidence_schema_version', None)))}",
        f"evidence_signature={result.evidence_signature}",
        f"evidence_as_of={evidence_as_of}",
        f"review_policy_schema_version={getattr(artifact, 'review_policy_schema_version', getattr(approval, 'review_policy_schema_version', getattr(review, 'review_policy_schema_version', None)))}",
        f"review_policy_signature={result.review_policy_signature}",
        f"review_evaluated_at={review_evaluated_at}",
        f"review_decision_signature={result.review_decision_signature}",
        f"eligible_for_full_live_review={_bool(result.review_status == 'ELIGIBLE_FOR_FULL_LIVE_REVIEW')}",
        f"approval_status={result.approval_status}",
        f"full_live_promotion_approval_id={getattr(approval, 'id', None)}",
        f"approval_source={getattr(approval, 'approval_source', None)}",
        f"human_approved_at={getattr(approval, 'human_approved_at', None)}",
        f"approval_signature={getattr(approval, 'approval_signature', None)}",
        f"confirmation_required={_bool(result.confirmation_required)}",
        f"confirmation_matched={_bool(result.confirmation_matched)}",
        f"human_approval_recorded={_bool(result.human_approval_recorded)}",
        f"full_live_promotion_approval_created={_bool(result.full_live_promotion_approval_created)}",
        f"full_live_promotion_approval_persisted={_bool(result.full_live_promotion_approval_persisted)}",
        f"full_live_policy_activated={_bool(result.full_live_policy_activated)}",
        f"database_write={_bool(result.database_write)}",
        f"external_calls={_bool(result.external_calls)}",
        f"live_policy_change={_bool(result.live_policy_change)}",
        f"live_order_change={_bool(result.live_order_change)}",
        f"ranking_runtime_changed={_bool(result.ranking_runtime_changed)}",
        f"canary_state_changed={_bool(result.canary_state_changed)}",
        "preview_signature_reusable_for_apply=false",
        f"safe_reason={result.safe_reason}",
    ]


def run(
    session_factory,
    namespace: argparse.Namespace,
    *,
    interactive: bool,
    input_fn=input,
    output_fn=print,
) -> tuple[list[str], int]:
    def confirm(artifact):
        for line in artifact_summary(artifact):
            output_fn(line)
        output_fn(
            "To approve this exact Full LIVE review, enter the exact "
            "review_decision_signature:"
        )
        return input_fn("> ")

    result = FullLivePolicyPromotionApprovalWorkflow(session_factory).execute(
        canary_activation_id=namespace.canary_activation_id,
        apply=namespace.apply,
        interactive=interactive,
        confirmation_fn=confirm if namespace.apply else None,
    )
    lines = report(result)
    if not namespace.apply:
        lines.extend(
            (
                "preview_notice=This preview is informational only.",
                "preview_notice=Its review_decision_signature must NOT be reused by a later --apply invocation.",
                "preview_notice=--apply creates and confirms one fresh exact Review inside the same process.",
            )
        )
    exit_code = (
        1 if result.approval_status == INVALID_FULL_LIVE_PROMOTION_APPROVAL else 0
    )
    return lines, exit_code


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        lines, exit_code = run(
            SessionLocal,
            namespace,
            interactive=sys.stdin.isatty(),
        )
        for line in lines:
            print(line)
        return exit_code
    except Exception as error:
        print(f"Full LIVE promotion approval failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
