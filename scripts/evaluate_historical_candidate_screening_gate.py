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
from crypto_trading_bot.services.historical_candidate_screening_gate_service import (
    INVALID_SCREENING_DATA,
    HistoricalCandidateScreeningGateResult,
    HistoricalCandidateScreeningGateService,
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


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Screen every novel reference-bounded historical candidate against "
            "the promotion policy's fixed historical thresholds."
        )
    )
    parser.add_argument(
        "--reference-snapshot-id", type=_positive_integer, required=True
    )
    parser.add_argument("--step", type=_step, default=DEFAULT_STEP)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    namespace = parser.parse_args(args)
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


def result_document(result: HistoricalCandidateScreeningGateResult):
    return _json_value(asdict(result))


def write_result_file(path: str | Path, document, *, force: bool):
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
    lines = [
        f"report_type={result.report_type}",
        f"gate_schema_version={result.gate_schema_version}",
        f"screening_policy_schema_version={result.screening_policy_schema_version}",
        f"screening_policy_signature={result.screening_policy_signature}",
        f"reference_snapshot_id={result.reference_snapshot_id}",
        f"historical_evidence_as_of={result.historical_evidence_as_of}",
        f"candidate_count={result.candidate_count}",
        f"pass_count={result.pass_count}",
        f"fail_count={result.fail_count}",
        f"insufficient_count={result.insufficient_count}",
        f"invalid_count={result.invalid_count}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
        f"output_path={output_path}",
        f"research_only={str(result.research_only).lower()}",
        (
            "historical_screening_performed="
            f"{str(result.historical_screening_performed).lower()}"
        ),
        (
            "automatic_policy_selection="
            f"{str(result.automatic_policy_selection).lower()}"
        ),
        (
            "candidate_registration_performed="
            f"{str(result.candidate_registration_performed).lower()}"
        ),
        f"database_write={str(result.database_write).lower()}",
        f"external_calls={str(result.external_calls).lower()}",
        f"live_policy_change={str(result.live_policy_change).lower()}",
    ]
    for candidate in result.candidate_results:
        lines.extend(
            (
                f"candidate_name={candidate.scenario_name}",
                (
                    "candidate_definition_signature="
                    f"{candidate.scenario_definition_signature}"
                ),
                f"candidate_donor={candidate.donor_field}",
                f"candidate_receiver={candidate.receiver_field}",
                f"candidate_status={candidate.status}",
                f"candidate_passed_check_count={len(candidate.passed_checks)}",
                (
                    "candidate_insufficient_check_count="
                    f"{len(candidate.insufficient_checks)}"
                ),
                f"candidate_failed_check_count={len(candidate.failed_checks)}",
                f"candidate_invalid_check_count={len(candidate.invalid_checks)}",
            )
        )
    return lines


def run(session, namespace):
    result = HistoricalCandidateScreeningGateService(session).evaluate(
        reference_snapshot_id=namespace.reference_snapshot_id,
        step=namespace.step,
    )
    output_path = None
    if namespace.output is not None:
        output_path = write_result_file(
            namespace.output, result_document(result), force=namespace.force
        )
    return report(result, output_path=output_path), (
        2 if result.status == INVALID_SCREENING_DATA else 0
    )


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            try:
                lines, exit_code = run(session, namespace)
            finally:
                session.rollback()
            for line in lines:
                print(line)
            return exit_code
    except FileExistsError as error:
        print(f"Historical candidate screening rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            f"Historical candidate screening failed. error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
