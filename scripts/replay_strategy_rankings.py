import argparse
from decimal import Decimal

from crypto_trading_bot.services.offline_strategy_replay_service import (
    BatchReplayResult,
    OfflineStrategyReplayService,
    ReplayInputError,
    SnapshotReplayResult,
    parse_override,
)


def _override_argument(value: str) -> tuple[str, Decimal]:
    try:
        return parse_override(value)
    except ReplayInputError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay ranking within persisted Strategy Replay prefilter pools (DB-only)."
        )
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--snapshot-id", type=int)
    selection.add_argument("--latest", type=int)
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        type=_override_argument,
        metavar="FIELD=VALUE",
    )
    parser.add_argument("--top-n", type=int)
    namespace = parser.parse_args(args)
    if namespace.snapshot_id is not None and namespace.snapshot_id < 1:
        parser.error("--snapshot-id must be >= 1")
    if namespace.latest is not None and namespace.latest < 1:
        parser.error("--latest must be >= 1")
    if namespace.top_n is not None and namespace.top_n < 1:
        parser.error("--top-n must be >= 1")
    override_names = [field_name for field_name, _ in namespace.override]
    if len(override_names) != len(set(override_names)):
        parser.error("each --override field may be specified only once")
    return namespace


def _csv(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "None"


def snapshot_report(result: SnapshotReplayResult) -> list[str]:
    lines = [
        "report_type=OFFLINE_STRATEGY_REPLAY",
        "result_type=RANKING_COUNTERFACTUAL_ONLY",
        "performance_evaluation=false",
        f"snapshot_id={result.snapshot_id}",
        f"pipeline_run_id={result.pipeline_run_id}",
        f"captured_at={result.captured_at}",
        f"dataset_schema_version={result.dataset_schema_version}",
        f"baseline_policy_signature={result.baseline_policy_signature}",
        f"scenario_signature={result.scenario_signature}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
        (
            "baseline_integrity=PASS"
            if result.baseline_matches_stored
            else "baseline_integrity=FAIL"
        ),
        f"rankable_candidate_count={result.rankable_candidate_count}",
        f"held_augmented_count={result.held_augmented_count}",
        f"stored_top_n={result.stored_top_n}",
        f"requested_top_n={result.requested_top_n}",
        f"effective_top_n={result.effective_top_n}",
        f"baseline_top_markets={_csv(result.baseline_top_markets)}",
        f"scenario_top_markets={_csv(result.scenario_top_markets)}",
        f"top_n_overlap_count={result.top_n_overlap_count}",
        f"top_n_overlap_rate={result.top_n_overlap_rate}",
        f"entered_top_n={_csv(result.entered_top_n)}",
        f"exited_top_n={_csv(result.exited_top_n)}",
    ]
    for candidate in result.candidate_results:
        lines.append(
            f"market={candidate.market} original_rank={candidate.original_rank} "
            f"baseline_rank={candidate.baseline_replay_rank} "
            f"scenario_rank={candidate.scenario_rank} "
            f"original_score={candidate.original_score} "
            f"baseline_score={candidate.baseline_replay_score} "
            f"scenario_score={candidate.scenario_score} "
            f"rank_delta={candidate.rank_delta_vs_original} "
            f"score_delta={candidate.score_delta_vs_original} "
            f"original_final_selected={candidate.original_final_selected} "
            f"original_ranked_selected={candidate.original_ranked_selected} "
            f"scenario_selected={candidate.scenario_selected} "
            f"entered_top_n={candidate.entered_top_n} "
            f"exited_top_n={candidate.exited_top_n}"
        )
    for diagnostic in result.mismatch_diagnostics:
        lines.append(
            f"baseline_mismatch_market={diagnostic.market} "
            f"stored_original_rank={diagnostic.stored_original_rank} "
            f"replay_baseline_rank={diagnostic.replay_baseline_rank} "
            f"stored_original_score={diagnostic.stored_original_score} "
            f"replay_baseline_score={diagnostic.replay_baseline_score} "
            f"score_delta={diagnostic.score_delta}"
        )
    return lines


def batch_report(result: BatchReplayResult) -> list[str]:
    lines = [
        "report_type=OFFLINE_STRATEGY_REPLAY_BATCH",
        "result_type=RANKING_COUNTERFACTUAL_ONLY",
        "performance_evaluation=false",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"replayed_snapshot_count={result.replayed_snapshot_count}",
        f"compatible_snapshot_count={result.compatible_snapshot_count}",
        f"incompatible_snapshot_count={result.incompatible_snapshot_count}",
        f"baseline_match_count={result.baseline_match_count}",
        f"baseline_mismatch_count={result.baseline_mismatch_count}",
        f"mean_top_n_overlap_rate={result.mean_top_n_overlap_rate}",
        f"mean_absolute_rank_change={result.mean_absolute_rank_change}",
        f"total_entered_top_n={result.total_entered_top_n}",
        f"total_exited_top_n={result.total_exited_top_n}",
    ]
    for replay in result.results:
        lines.append(
            f"snapshot_id={replay.snapshot_id} status={replay.status} "
            f"safe_reason={replay.safe_reason} "
            f"top_n_overlap_rate={replay.top_n_overlap_rate}"
        )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = OfflineStrategyReplayService(session)
    overrides = dict(namespace.override)
    if namespace.snapshot_id is not None:
        result = service.replay_snapshot(
            namespace.snapshot_id, overrides=overrides, top_n=namespace.top_n
        )
        return snapshot_report(result), 0 if result.status == "SUCCESS" else 1
    result = service.replay_latest(
        namespace.latest if namespace.latest is not None else 1,
        overrides=overrides,
        top_n=namespace.top_n,
    )
    exit_code = (
        0
        if result.incompatible_snapshot_count == 0
        and result.baseline_mismatch_count == 0
        else 1
    )
    return batch_report(result), exit_code


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
        print(f"Offline strategy replay rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Offline strategy replay failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
