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
from crypto_trading_bot.services.reference_bounded_historical_research_batch_service import (
    INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH,
    ReferenceBoundedHistoricalResearchBatchResult,
    ReferenceBoundedHistoricalResearchBatchService,
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
            "Evaluate every novel generated ranking candidate against one "
            "reference-time-bounded DB-only historical research scope."
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


def result_document(
    result: ReferenceBoundedHistoricalResearchBatchResult,
) -> dict[str, object]:
    return _json_value(asdict(result))


def write_result_file(path: str | Path, document: dict[str, object], *, force: bool):
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
    profile = result.research_profile
    assumptions = profile.assumptions
    lines = [
        f"report_type={result.report_type}",
        f"batch_schema_version={result.batch_schema_version}",
        f"research_profile_schema_version={profile.schema_version}",
        f"reference_snapshot_id={result.reference_snapshot_id}",
        f"historical_evidence_as_of={result.historical_evidence_as_of}",
        f"user_id={result.user_id}",
        f"exchange={result.exchange}",
        f"quote_asset={result.quote_asset}",
        f"reference_policy_signature={result.reference_policy_signature}",
        f"effective_top_n={result.effective_top_n}",
        f"generator_step={result.generator_step}",
        f"generated_candidate_count={result.generated_candidate_count}",
        (
            "already_registered_candidate_count="
            f"{result.already_registered_candidate_count}"
        ),
        f"novel_candidate_count={result.novel_candidate_count}",
        f"evaluated_candidate_count={result.evaluated_candidate_count}",
        (
            "historical_timeline_snapshot_count="
            f"{result.historical_timeline_snapshot_count}"
        ),
        (
            "historical_context_snapshot_count="
            f"{result.historical_context_snapshot_count}"
        ),
        f"horizons={','.join(str(value) for value in profile.horizons)}",
        f"initial_research_size={profile.initial_research_size}",
        f"validation_size={profile.validation_size}",
        f"fee_rate={assumptions.fee_rate}",
        f"spread_cost_rate={assumptions.spread_cost_rate}",
        f"slippage_rate={assumptions.slippage_rate}",
        f"total_cost_rate={assumptions.total_cost_rate}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
        f"output_path={output_path}",
        f"research_only={str(result.research_only).lower()}",
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
        f"live_order_change={str(result.live_order_change).lower()}",
    ]
    for candidate in result.candidate_results:
        gross_statuses = ",".join(item.gross_status for item in candidate.horizons)
        cost_statuses = ",".join(
            item.cost_adjusted_status for item in candidate.horizons
        )
        lines.extend(
            (
                f"candidate_name={candidate.scenario_name}",
                (
                    "candidate_definition_signature="
                    f"{candidate.scenario_definition_signature}"
                ),
                f"candidate_donor={candidate.donor_field}",
                f"candidate_receiver={candidate.receiver_field}",
                f"candidate_step={candidate.transfer_step}",
                f"candidate_horizon_count={len(candidate.horizons)}",
                f"candidate_turnover_status={candidate.turnover_status}",
                f"candidate_gross_statuses={gross_statuses}",
                f"candidate_cost_statuses={cost_statuses}",
            )
        )
    return lines


def run(session, namespace):
    result = ReferenceBoundedHistoricalResearchBatchService(session).evaluate(
        reference_snapshot_id=namespace.reference_snapshot_id,
        step=namespace.step,
    )
    output_path = None
    if namespace.output is not None:
        output_path = write_result_file(
            namespace.output, result_document(result), force=namespace.force
        )
    exit_code = (
        2 if result.status == INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH else 0
    )
    return report(result, output_path=output_path), exit_code


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
        print(f"Historical research batch rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Historical research batch failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
