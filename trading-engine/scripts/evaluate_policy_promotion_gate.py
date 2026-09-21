import argparse
import json
from dataclasses import fields
from decimal import Decimal

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.policy_promotion_gate_service import (
    INVALID_PROMOTION_DATA,
    POLICY_PROMOTION_GATE_V1,
    RESULT_TYPE,
    PolicyPromotionGateResult,
    PolicyPromotionGateService,
)


REPORT_TYPE = "POLICY_PROMOTION_GATE"


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
        description="Evaluate the immutable v1 engineering gate for one registered policy candidate."
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    return parser.parse_args(args)


def _value(value) -> str:
    if isinstance(value, tuple):
        return ",".join(str(item) for item in value)
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _weights(values: dict[str, Decimal]) -> str:
    return json.dumps(
        {name: _value(value) for name, value in sorted(values.items())},
        sort_keys=True,
        separators=(",", ":"),
    )


def report(result: PolicyPromotionGateResult) -> list[str]:
    candidate = result.candidate
    historical = result.historical
    gross = result.forward_gross
    turnover = result.forward_turnover
    lines = [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        f"gate_policy_schema_version={result.gate_policy_schema_version}",
        f"gate_policy_signature={result.gate_policy_signature}",
        "research_only=true",
        f"sample_sufficiency_assessed={_value(result.sample_sufficiency_assessed)}",
        f"statistical_inference_performed={_value(result.statistical_inference_performed)}",
        f"policy_decision_performed={_value(result.policy_decision_performed)}",
        f"promotion_performed={_value(result.promotion_performed)}",
        f"shadow_policy_created={_value(result.shadow_policy_created)}",
        f"database_write={_value(result.database_write)}",
        f"external_calls={_value(result.external_calls)}",
        f"live_policy_change={_value(result.live_policy_change)}",
        f"candidate_id={result.candidate_id}",
        f"candidate_schema_version={getattr(candidate, 'candidate_schema_version', None)}",
        f"scenario_name={getattr(candidate, 'scenario_name', None)}",
        f"scenario_definition_signature={getattr(candidate, 'scenario_definition_signature', None)}",
        (
            f"component_weights={_weights(candidate.component_weights)}"
            if candidate is not None
            else "component_weights=None"
        ),
        f"user_id={getattr(candidate, 'user_id', None)}",
        f"exchange={getattr(candidate, 'exchange', None)}",
        f"quote_asset={getattr(candidate, 'quote_asset', None)}",
        f"baseline_policy_signature={getattr(candidate, 'baseline_policy_signature', None)}",
        f"effective_top_n={getattr(candidate, 'effective_top_n', None)}",
        f"reference_snapshot_id={getattr(candidate, 'reference_snapshot_id', None)}",
        f"registered_at={getattr(candidate, 'registered_at', None)}",
        f"registration_snapshot_id_watermark={getattr(candidate, 'registration_snapshot_id_watermark', None)}",
        f"registration_captured_at_watermark={getattr(candidate, 'registration_captured_at_watermark', None)}",
        f"evaluated_at={result.evaluated_at.isoformat()}",
        f"forward_snapshot_id_ceiling={result.forward_snapshot_id_ceiling}",
        f"historical_evidence_as_of={getattr(historical, 'historical_evidence_as_of', None)}",
        f"historical_strict_unseen_validation={getattr(historical, 'historical_strict_unseen_validation', None)}",
        f"historical_snapshot_count={len(getattr(historical, 'historical_candidate_snapshot_ids', ()))}",
        f"forward_snapshot_count={len(getattr(gross, 'eligible_forward_snapshot_ids', ()))}",
        f"forward_turnover_transition_count={getattr(turnover, 'transition_count', 0)}",
    ]
    for field in fields(POLICY_PROMOTION_GATE_V1):
        lines.append(
            f"{field.name}={_value(getattr(POLICY_PROMOTION_GATE_V1, field.name))}"
        )
    for index, check in enumerate(result.all_checks, start=1):
        prefix = f"check_{index}"
        lines.extend(
            (
                f"{prefix}_id={check.check_id}",
                f"{prefix}_category={check.category}",
                f"{prefix}_horizon_minutes={check.horizon_minutes}",
                f"{prefix}_status={check.status}",
                f"{prefix}_observed_value={check.observed_value}",
                f"{prefix}_comparator={check.comparator}",
                f"{prefix}_threshold_value={check.threshold_value}",
                f"{prefix}_reason={check.reason}",
            )
        )
    lines.extend(
        (
            f"passed_check_count={len(result.passed_checks)}",
            f"insufficient_check_count={len(result.insufficient_checks)}",
            f"failed_check_count={len(result.failed_checks)}",
            f"invalid_check_count={len(result.invalid_checks)}",
            f"status={result.status}",
            f"safe_reason={result.safe_reason}",
        )
    )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = PolicyPromotionGateService(session).evaluate(
        candidate_id=namespace.candidate_id
    )
    return report(result), 1 if result.status == INVALID_PROMOTION_DATA else 0


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
        print(f"Policy promotion gate rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Policy promotion gate failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
