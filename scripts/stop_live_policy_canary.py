import argparse

from crypto_trading_bot.services.live_policy_canary_termination_service import (
    INVALID_CANARY_TERMINATION,
    REPORT_TYPE,
    STOPPED,
    LivePolicyCanaryTerminationService,
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
        description="Preview or explicitly stop Limited LIVE Canary v1-B."
    )
    parser.add_argument("--canary-activation-id", type=_positive_integer, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-activation-signature")
    namespace = parser.parse_args(args)
    if namespace.apply and namespace.expected_activation_signature is None:
        parser.error("--apply requires --expected-activation-signature")
    if not namespace.apply and namespace.expected_activation_signature is not None:
        parser.error("--expected-activation-signature is only valid with --apply")
    return namespace


def _bool(value):
    return str(bool(value)).lower()


def report(result):
    activation = result.activation
    binding = result.safety_binding
    event = result.termination_event
    return [
        f"report_type={REPORT_TYPE}",
        f"termination_status={result.termination_status}",
        f"activation_id={getattr(activation, 'id', None)}",
        f"activation_signature={getattr(activation, 'activation_signature', None)}",
        f"safety_binding_id={getattr(binding, 'id', None)}",
        f"safety_binding_signature={getattr(binding, 'binding_signature', None)}",
        f"promotion_approval_id={getattr(activation, 'promotion_approval_id', None)}",
        f"candidate_id={getattr(activation, 'candidate_id', None)}",
        f"current_mode={result.current_mode}",
        f"termination_source={getattr(event, 'termination_source', 'MANUAL_CLI')}",
        f"termination_reason={getattr(event, 'termination_reason', 'MANUAL_STOP')}",
        f"terminated_at={getattr(event, 'terminated_at', result.proposed_terminated_at)}",
        f"termination_signature={getattr(event, 'termination_signature', None)}",
        f"database_write={_bool(result.database_write)}",
        f"external_calls={_bool(result.external_calls)}",
        f"ranking_runtime_changed={_bool(result.ranking_runtime_changed)}",
        f"order_cancelled={_bool(result.order_cancelled)}",
        f"safe_reason={result.safe_reason}",
    ]


def run(session, namespace):
    service = LivePolicyCanaryTerminationService(session)
    result = (
        service.stop(
            canary_activation_id=namespace.canary_activation_id,
            expected_activation_signature=namespace.expected_activation_signature,
        )
        if namespace.apply
        else service.preview(canary_activation_id=namespace.canary_activation_id)
    )
    if result.termination_status != STOPPED:
        session.rollback()
    return report(
        result
    ), 1 if result.termination_status == INVALID_CANARY_TERMINATION else 0


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
        print(f"Canary termination rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Canary termination failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
