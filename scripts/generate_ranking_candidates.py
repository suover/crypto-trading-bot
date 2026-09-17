import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile

from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_STEP,
    MAX_CANDIDATES,
    MAX_STEP,
    CandidateGenerationError,
    DeterministicRankingCandidateGenerationResult,
    DeterministicRankingCandidateGeneratorService,
    select_balanced_candidates,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION as SCENARIO_SCHEMA_VERSION,
)


REPORT_TYPE = "DETERMINISTIC_RANKING_CANDIDATE_GENERATION"


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _max_candidates(value: str) -> int:
    parsed = _positive_integer(value)
    if parsed > MAX_CANDIDATES:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_CANDIDATES}")
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
            "Generate deterministic ranking research candidates from one stored "
            "snapshot (DB-read-only)."
        )
    )
    parser.add_argument(
        "--reference-snapshot-id", type=_positive_integer, required=True
    )
    parser.add_argument("--step", type=_step, default=DEFAULT_STEP)
    parser.add_argument(
        "--max-candidates", type=_max_candidates, default=DEFAULT_MAX_CANDIDATES
    )
    parser.add_argument("--output")
    parser.add_argument("--include-registered", action="store_true")
    parser.add_argument("--force", action="store_true")
    namespace = parser.parse_args(args)
    if namespace.force and namespace.output is None:
        parser.error("--force requires --output")
    return namespace


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical_weights(weights: dict[str, Decimal]) -> dict[str, str]:
    return {name: _canonical_decimal(value) for name, value in sorted(weights.items())}


def scenario_document(
    result: DeterministicRankingCandidateGenerationResult,
    *,
    include_registered: bool,
) -> dict[str, object]:
    selection_pool = tuple(
        candidate
        for candidate in result.all_candidates
        if include_registered or not candidate.already_registered
    )
    selected = select_balanced_candidates(
        selection_pool, max_candidates=result.max_candidates
    )
    if not selected:
        raise CandidateGenerationError(
            "no generated candidates are eligible for scenario output"
        )
    return {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenarios": [
            {
                "name": candidate.scenario_name,
                "component_weights": _canonical_weights(candidate.component_weights),
            }
            for candidate in selected
        ],
    }


def write_scenario_file(
    path: str | Path, document: dict[str, object], *, force: bool
) -> Path:
    destination = Path(path)
    if destination.exists() and not force:
        raise CandidateGenerationError(
            f"output file already exists (use --force to replace): {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
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


def report(
    result: DeterministicRankingCandidateGenerationResult,
    *,
    output_path: Path | None,
    output_candidate_count: int,
) -> list[str]:
    lines = [
        f"report_type={REPORT_TYPE}",
        f"generator_schema_version={result.generator_schema_version}",
        f"reference_snapshot_id={result.reference_snapshot_id}",
        f"user_id={result.user_id}",
        f"exchange={result.exchange}",
        f"quote_asset={result.quote_asset}",
        f"dataset_schema_version={result.dataset_schema_version}",
        f"reference_policy_signature={result.reference_policy_signature}",
        f"effective_top_n={result.effective_top_n}",
        f"step={result.step}",
        f"max_candidates={result.max_candidates}",
        f"generated_before_dedup_count={result.generated_before_dedup_count}",
        f"generated_valid_count={result.generated_valid_count}",
        f"returned_candidate_count={result.returned_candidate_count}",
        f"duplicate_removed_count={result.duplicate_removed_count}",
        f"already_registered_count={result.already_registered_count}",
        f"novel_candidate_count={result.novel_candidate_count}",
        f"selection_includes_registered={str(result.selection_includes_registered).lower()}",
        f"candidate_cap_applied={str(result.candidate_cap_applied).lower()}",
        f"output_candidate_count={output_candidate_count}",
        f"output_path={output_path}",
        f"database_write={str(result.database_write).lower()}",
        f"external_calls={str(result.external_calls).lower()}",
        f"outcome_data_used={str(result.outcome_data_used).lower()}",
        f"performance_evaluated={str(result.performance_evaluated).lower()}",
        (f"policy_decision_performed={str(result.policy_decision_performed).lower()}"),
        f"promotion_performed={str(result.promotion_performed).lower()}",
        f"shadow_runtime_changed={str(result.shadow_runtime_changed).lower()}",
        f"live_policy_change={str(result.live_policy_change).lower()}",
        f"live_order_change={str(result.live_order_change).lower()}",
    ]
    for candidate in result.candidates:
        lines.extend(
            (
                f"candidate_name={candidate.scenario_name}",
                (
                    "candidate_definition_signature="
                    f"{candidate.scenario_definition_signature}"
                ),
                f"candidate_donor_field={candidate.donor_field}",
                f"candidate_receiver_field={candidate.receiver_field}",
                f"candidate_transfer_step={candidate.transfer_step}",
                (
                    "candidate_component_weights="
                    f"{json.dumps(_canonical_weights(candidate.component_weights), sort_keys=True, separators=(',', ':'))}"
                ),
                (
                    "candidate_already_registered="
                    f"{str(candidate.already_registered).lower()}"
                ),
            )
        )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = DeterministicRankingCandidateGeneratorService(session).generate(
        reference_snapshot_id=namespace.reference_snapshot_id,
        step=namespace.step,
        max_candidates=namespace.max_candidates,
        include_registered=namespace.include_registered,
    )
    output_path = None
    output_candidate_count = 0
    if namespace.output is not None:
        document = scenario_document(
            result, include_registered=namespace.include_registered
        )
        output_candidate_count = len(document["scenarios"])
        output_path = write_scenario_file(
            namespace.output, document, force=namespace.force
        )
    return (
        report(
            result,
            output_path=output_path,
            output_candidate_count=output_candidate_count,
        ),
        0,
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
    except CandidateGenerationError as error:
        print(f"Ranking candidate generation rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Ranking candidate generation failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
