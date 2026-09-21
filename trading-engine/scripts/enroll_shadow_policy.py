import argparse
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    CREATED,
    INVALID_SHADOW_ENROLLMENT,
    ShadowPolicyEnrollmentResult,
    ShadowPolicyEnrollmentService,
)


REPORT_TYPE = "SHADOW_POLICY_ENROLLMENT"


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
        description="Anchor an eligible research candidate for future Shadow evaluation."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(args)


def _bool(value: bool) -> str:
    return str(value).lower()


def report(result: ShadowPolicyEnrollmentResult) -> list[str]:
    candidate = result.candidate
    enrollment = result.enrollment
    gate = result.gate
    gate_status = (
        gate.status if gate is not None else getattr(enrollment, "gate_status", None)
    )
    lines = [
        f"report_type={REPORT_TYPE}",
        f"enrollment_schema_version={result.enrollment_schema_version}",
        "shadow_runtime_enabled=false",
        f"shadow_evaluation_started={_bool(result.shadow_evaluation_started)}",
        f"promotion_performed={_bool(result.promotion_performed)}",
        f"live_policy_change={_bool(result.live_policy_change)}",
        f"external_calls={_bool(result.external_calls)}",
        f"database_write={_bool(result.database_write)}",
        f"candidate_id={result.candidate_id}",
        f"candidate_schema_version={getattr(candidate, 'candidate_schema_version', None)}",
        f"scenario_name={getattr(candidate, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(candidate, 'scenario_definition_signature', None)}",
        f"component_weights={json.dumps(getattr(candidate, 'component_weights', None), default=str, sort_keys=True, separators=(',', ':'))}",
        f"user_id={getattr(candidate, 'user_id', None)}",
        f"exchange={getattr(candidate, 'exchange', None)}",
        f"quote_asset={getattr(candidate, 'quote_asset', None)}",
        f"dataset_schema_version={getattr(candidate, 'dataset_schema_version', None)}",
        f"baseline_policy_signature={getattr(candidate, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(candidate, 'effective_top_n', None)}",
        f"candidate_registered_at={getattr(candidate, 'registered_at', None)}",
        f"candidate_registration_snapshot_id_watermark={getattr(candidate, 'registration_snapshot_id_watermark', None)}",
        f"candidate_registration_captured_at_watermark={getattr(candidate, 'registration_captured_at_watermark', None)}",
        f"gate_status={gate_status}",
        f"gate_result_type={getattr(enrollment, 'gate_result_type', 'POLICY_PROMOTION_GATE_V1_DECISION' if gate else None)}",
        f"gate_policy_schema_version={getattr(gate, 'gate_policy_schema_version', getattr(enrollment, 'gate_policy_schema_version', None))}",
        f"gate_policy_signature={getattr(gate, 'gate_policy_signature', getattr(enrollment, 'gate_policy_signature', None))}",
        f"gate_evaluated_at={getattr(gate, 'evaluated_at', getattr(enrollment, 'gate_evaluated_at', None))}",
        f"gate_forward_snapshot_id_ceiling={getattr(gate, 'forward_snapshot_id_ceiling', getattr(enrollment, 'gate_forward_snapshot_id_ceiling', None))}",
        f"gate_decision_signature={result.gate_decision_signature}",
        f"shadow_enrolled_at={getattr(enrollment, 'shadow_enrolled_at', None)}",
        f"shadow_snapshot_id_watermark={getattr(enrollment, 'shadow_snapshot_id_watermark', None)}",
        f"shadow_captured_at_watermark={getattr(enrollment, 'shadow_captured_at_watermark', None)}",
        f"preview_only={_bool(result.enrollment_status == 'DRY_RUN')}",
        f"anchor_persisted={_bool(result.shadow_enrollment_persisted)}",
        f"gate_evaluated={_bool(result.gate_evaluated)}",
        f"gate_eligible={_bool(result.gate_eligible)}",
        f"enrollment_status={result.enrollment_status}",
        f"safe_reason={result.safe_reason}",
    ]
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = ShadowPolicyEnrollmentService(session)
    result = (
        service.enroll(candidate_id=namespace.candidate_id)
        if namespace.apply
        else service.preview(candidate_id=namespace.candidate_id)
    )
    if namespace.apply and result.enrollment_status == CREATED:
        session.commit()
    else:
        session.rollback()
    return (
        report(result),
        1 if result.enrollment_status == INVALID_SHADOW_ENROLLMENT else 0,
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
        print(f"Shadow policy enrollment rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Shadow policy enrollment failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
