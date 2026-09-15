import argparse
import json

from crypto_trading_bot.services.live_canary_evidence_service import (
    INVALID_CANARY_EVIDENCE,
    LiveCanaryEvidenceService,
)
from crypto_trading_bot.services.live_policy_canary_service import _canonicalize


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
        description="Evaluate persisted LIVE Canary evidence read-only."
    )
    parser.add_argument("--canary-activation-id", type=_positive_integer, required=True)
    return parser.parse_args(args)


def report(result):
    payload = result.as_dict()
    run_summary = payload["run_summary"]
    recommendation_summary = payload["recommendation_evidence"]["summary"]
    approval_summary = payload["approval_evidence"]["summary"]
    order_summary = payload["order_evidence"]["summary"]
    fill_summary = payload["fill_evidence"]["summary"]
    alert_summary = payload["operational_evidence"]["summary"]
    financial = payload["direct_canary_financial_facts"]
    lines = [
        f"report_type={payload['report_type']}",
        f"evidence_schema_version={payload['evidence_schema_version']}",
        f"canary_activation_id={payload['canary_activation_id']}",
        f"candidate_id={payload['candidate_id']}",
        f"status={payload['status']}",
        f"lifecycle_state={payload['lifecycle_state']}",
        f"evidence_as_of={payload['evidence_as_of']}",
        f"evidence_signature={payload['evidence_signature']}",
        f"snapshot_consistency={payload['snapshot_consistency']}",
    ]
    lines.extend(f"{name}={value}" for name, value in result.ceilings.items())
    for summary in (
        run_summary,
        recommendation_summary,
        approval_summary,
        order_summary,
        fill_summary,
        alert_summary,
    ):
        lines.extend(f"{name}={value}" for name, value in summary.items())
    for name in (
        "submitted_buy_amount_krw",
        "gross_buy_executed_funds_krw",
        "gross_sell_executed_funds_krw",
        "total_fee_krw",
    ):
        if name in financial:
            lines.append(f"{name}={financial[name]}")
    lines.extend(
        f"{name}={str(value).lower()}"
        for name, value in payload["safety_flags"].items()
    )
    lines.append(
        "evidence_json="
        + json.dumps(
            _canonicalize(payload),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return lines


def run(session, namespace):
    result = LiveCanaryEvidenceService(session).evaluate(
        canary_activation_id=namespace.canary_activation_id
    )
    session.rollback()
    return report(result), 1 if result.status == INVALID_CANARY_EVIDENCE else 0


def main(args=None):
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            lines, exit_code = run(session, namespace)
            for line in lines:
                print(line)
            return exit_code
    except Exception as error:
        print(f"Canary evidence failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
