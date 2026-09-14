import argparse

from crypto_trading_bot.services.live_policy_canary_service import (
    CREATED,
    INVALID_CANARY_ACTIVATION,
    REPORT_TYPE,
    LivePolicyCanaryActivationService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_arguments(args=None):
    parser = argparse.ArgumentParser(
        description="Preview or explicitly start Limited LIVE Canary v1-B."
    )
    parser.add_argument(
        "--promotion-approval-id", type=_positive_integer, required=True
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-approval-signature")
    namespace = parser.parse_args(args)
    if namespace.apply and namespace.expected_approval_signature is None:
        parser.error("--apply requires --expected-approval-signature")
    if not namespace.apply and namespace.expected_approval_signature is not None:
        parser.error("--expected-approval-signature is only valid with --apply")
    return namespace


def _bool(value):
    return str(bool(value)).lower()


def report(result):
    activation = result.activation
    binding = result.safety_binding
    approval = result.approval
    return [
        f"report_type={REPORT_TYPE}",
        f"activation_status={result.activation_status}",
        f"promotion_approval_id={getattr(approval, 'id', None)}",
        f"promotion_approval_signature={getattr(approval, 'approval_signature', None)}",
        f"expected_approval_signature={result.expected_approval_signature}",
        f"approval_signature_matched={_bool(result.approval_signature_matched)}",
        f"candidate_id={getattr(approval, 'candidate_id', None)}",
        f"shadow_enrollment_id={getattr(approval, 'shadow_enrollment_id', None)}",
        f"scenario_name={getattr(approval, 'scenario_name', None)}",
        f"baseline_policy_signature={getattr(activation, 'baseline_policy_signature', getattr(approval, 'baseline_policy_signature', None))}",
        f"canary_policy_signature={getattr(activation, 'canary_policy_signature', None)}",
        f"effective_top_n={getattr(approval, 'effective_top_n', None)}",
        f"canary_policy_schema_version={result.canary_policy_schema_version}",
        f"canary_policy_definition={result.canary_policy_definition}",
        f"canary_policy_definition_signature={result.canary_policy_definition_signature}",
        f"started_at={getattr(activation, 'started_at', result.proposed_started_at)}",
        f"expires_at={getattr(activation, 'expires_at', result.proposed_expires_at)}",
        f"max_analysis_runs={result.max_analysis_runs}",
        f"activation_signature={getattr(activation, 'activation_signature', None)}",
        f"safety_binding_id={getattr(binding, 'id', None)}",
        f"safety_binding_signature={getattr(binding, 'binding_signature', None)}",
        f"order_safety_policy_schema_version={result.order_safety_policy_schema_version}",
        f"order_safety_policy_signature={result.order_safety_policy_signature}",
        f"max_buy_order_amount_krw={result.max_buy_order_amount_krw}",
        f"daily_max_buy_amount_krw={result.daily_max_buy_amount_krw}",
        f"database_write={_bool(result.database_write)}",
        f"external_calls={_bool(result.external_calls)}",
        f"ranking_runtime_activation_record_created={_bool(result.ranking_runtime_activation_record_created)}",
        f"order_behavior_changed={_bool(result.order_behavior_changed)}",
        f"canary_order_cap_enabled={_bool(result.safety_binding is not None)}",
        f"safe_reason={result.safe_reason}",
    ]


def run(session, namespace):
    service = LivePolicyCanaryActivationService(session)
    result = (
        service.activate(
            promotion_approval_id=namespace.promotion_approval_id,
            expected_approval_signature=namespace.expected_approval_signature,
        )
        if namespace.apply
        else service.preview(promotion_approval_id=namespace.promotion_approval_id)
    )
    if result.activation_status != CREATED:
        session.rollback()
    return report(
        result
    ), 1 if result.activation_status == INVALID_CANARY_ACTIVATION else 0


def main(args=None):
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            lines, exit_code = run(session, namespace)
            for line in lines:
                print(line)
            return exit_code
    except ReplayInputError as error:
        print(f"Canary activation rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Canary activation failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
