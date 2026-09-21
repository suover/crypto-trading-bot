import argparse
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile

from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DEFAULT_STEP,
    MAX_STEP,
)
from crypto_trading_bot.services.screening_gated_research_candidate_registration_service import (
    CREATED,
    ScreeningGatedResearchCandidateRegistrationResult,
    ScreeningGatedResearchCandidateRegistrationService,
)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _step(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("step must be a Decimal") from error
    if not parsed.is_finite() or parsed <= 0 or parsed > MAX_STEP:
        raise argparse.ArgumentTypeError(
            f"step must be greater than 0 and at most {MAX_STEP}"
        )
    return parsed


def parse_arguments(args=None):
    parser = argparse.ArgumentParser(
        description="Preview or atomically register every freshly screened PASS candidate."
    )
    parser.add_argument(
        "--reference-snapshot-id", type=_positive_integer, required=True
    )
    parser.add_argument("--step", type=_step, default=DEFAULT_STEP)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-signature")
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    namespace = parser.parse_args(args)
    if namespace.apply and not namespace.expected_plan_signature:
        parser.error("--apply requires --expected-plan-signature")
    if not namespace.apply and namespace.expected_plan_signature:
        parser.error("--expected-plan-signature is only valid with --apply")
    if namespace.force and namespace.output is None:
        parser.error("--force requires --output")
    return namespace


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _json_value(value):
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("result contains a non-aware datetime")
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def result_document(result: ScreeningGatedResearchCandidateRegistrationResult):
    return _json_value(asdict(result))


def write_result_file(path, document, *, force):
    destination = Path(path)
    if destination.exists() and not force:
        raise FileExistsError(
            f"output file already exists (use --force to replace): {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return destination


def report(result, *, output_path):
    plan = result.registration_plan
    lines = [
        f"report_type={result.report_type}",
        f"schema_version={result.schema_version}",
        f"reference_snapshot_id={plan.reference_snapshot_id if plan else None}",
        f"historical_evidence_as_of={plan.historical_evidence_as_of if plan else None}",
        f"screening_policy_signature={plan.screening_policy_signature if plan else None}",
        (
            "source_promotion_policy_signature="
            f"{plan.source_promotion_policy_signature if plan else None}"
        ),
        f"screened_candidate_count={result.screened_candidate_count}",
        f"pass_candidate_count={result.pass_candidate_count}",
        f"registration_candidate_count={result.registration_candidate_count}",
        (
            "expected_registration_snapshot_id_watermark="
            f"{plan.expected_registration_snapshot_id_watermark if plan else None}"
        ),
        (
            "expected_registration_captured_at_watermark="
            f"{plan.expected_registration_captured_at_watermark if plan else None}"
        ),
        f"registration_plan_signature={result.registration_plan_signature}",
        f"apply_requested={str(result.apply_requested).lower()}",
        f"created_candidate_count={result.created_candidate_count}",
        f"shared_registered_at={result.shared_registered_at}",
        (
            "shared_registration_snapshot_id_watermark="
            f"{result.shared_registration_snapshot_id_watermark}"
        ),
        (
            "shared_registration_captured_at_watermark="
            f"{result.shared_registration_captured_at_watermark}"
        ),
        (
            "registration_performed="
            f"{str(result.candidate_registration_performed).lower()}"
        ),
        f"database_write={str(result.database_write).lower()}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
        f"output_path={output_path}",
    ]
    for candidate in result.candidate_results:
        lines.extend(
            (
                f"candidate_name={candidate.scenario_name}",
                (
                    "candidate_definition_signature="
                    f"{candidate.scenario_definition_signature}"
                ),
                f"candidate_registration_status={candidate.registration_status}",
                f"candidate_id={candidate.candidate_id}",
            )
        )
    return lines


def run(session, namespace):
    service = ScreeningGatedResearchCandidateRegistrationService(session)
    if namespace.apply:
        result = service.apply(
            reference_snapshot_id=namespace.reference_snapshot_id,
            step=namespace.step,
            expected_plan_signature=namespace.expected_plan_signature,
        )
    else:
        result = service.preview(
            reference_snapshot_id=namespace.reference_snapshot_id,
            step=namespace.step,
        )
    if result.status == CREATED:
        session.commit()
    else:
        session.rollback()
    output_path = None
    if namespace.output is not None:
        output_path = write_result_file(
            namespace.output, result_document(result), force=namespace.force
        )
    return report(result, output_path=output_path), (
        0 if result.status in {"READY", "CREATED", "NO_PASS_CANDIDATES"} else 2
    )


def main(args=None):
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            lines, exit_code = run(session, namespace)
            for line in lines:
                print(line)
            return exit_code
    except FileExistsError as error:
        print(f"Screening-gated registration rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Screening-gated registration failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
