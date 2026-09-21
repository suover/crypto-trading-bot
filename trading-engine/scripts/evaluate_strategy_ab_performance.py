import argparse
from decimal import Decimal

from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
    parse_override,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    RESULT_TYPE,
    StrategyABBatchPerformanceResult,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)


def _override_argument(value: str) -> tuple[str, Decimal]:
    try:
        return parse_override(value)
    except ReplayInputError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare baseline and scenario ranking selections using stored outcomes "
            "only (DB-only/read-only)."
        )
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--snapshot-id", type=int)
    selection.add_argument("--latest", type=int)
    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        type=_override_argument,
        metavar="FIELD=VALUE",
    )
    namespace = parser.parse_args(args)
    if namespace.snapshot_id is not None and namespace.snapshot_id < 1:
        parser.error("--snapshot-id must be >= 1")
    if namespace.latest is not None and namespace.latest < 1:
        parser.error("--latest must be >= 1")
    if namespace.horizon < 1:
        parser.error("--horizon must be >= 1")
    names = [name for name, _ in namespace.override]
    if len(names) != len(set(names)):
        parser.error("each --override field may be specified only once")
    return namespace


def _csv(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "None"


def _bool(value: bool) -> str:
    return str(value).lower()


def snapshot_report(result: StrategyABSnapshotPerformanceResult) -> list[str]:
    return [
        "report_type=STRATEGY_AB_PERFORMANCE",
        f"result_type={RESULT_TYPE}",
        f"snapshot_id={result.snapshot_id}",
        f"pipeline_run_id={result.pipeline_run_id}",
        f"captured_at={result.captured_at}",
        f"horizon_minutes={result.horizon_minutes}",
        f"baseline_policy_signature={result.baseline_policy_signature}",
        f"scenario_signature={result.scenario_signature}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
        f"performance_evaluated={_bool(result.performance_evaluated)}",
        (
            "baseline_integrity=PASS"
            if result.replay_status == "SUCCESS"
            and result.status != "BASELINE_INTEGRITY_FAILED"
            else "baseline_integrity=FAIL"
        ),
        (
            "outcome_coverage=PASS"
            if result.performance_evaluated
            else "outcome_coverage=FAIL"
        ),
        f"replay_status={result.replay_status}",
        f"replay_safe_reason={result.replay_safe_reason}",
        f"effective_top_n={result.effective_top_n}",
        f"baseline_top_markets={_csv(result.baseline_top_markets)}",
        f"scenario_top_markets={_csv(result.scenario_top_markets)}",
        f"top_n_overlap_count={result.top_n_overlap_count}",
        f"top_n_overlap_rate={result.top_n_overlap_rate}",
        f"entered_top_n={_csv(result.entered_top_n)}",
        f"exited_top_n={_csv(result.exited_top_n)}",
        f"baseline_complete_count={result.baseline_complete_count}",
        f"baseline_required_count={result.baseline_required_count}",
        f"baseline_missing_markets={_csv(result.baseline_missing_markets)}",
        f"scenario_complete_count={result.scenario_complete_count}",
        f"scenario_required_count={result.scenario_required_count}",
        f"scenario_missing_markets={_csv(result.scenario_missing_markets)}",
        f"baseline_candidate_count={result.baseline_candidate_count}",
        f"scenario_candidate_count={result.scenario_candidate_count}",
        f"baseline_mean_return={result.baseline_mean_return}",
        f"scenario_mean_return={result.scenario_mean_return}",
        f"mean_return_delta={result.mean_return_delta}",
        f"baseline_median_return={result.baseline_median_return}",
        f"scenario_median_return={result.scenario_median_return}",
        f"median_return_delta={result.median_return_delta}",
        f"baseline_positive_count={result.baseline_positive_count}",
        f"baseline_negative_count={result.baseline_negative_count}",
        f"baseline_flat_count={result.baseline_flat_count}",
        f"scenario_positive_count={result.scenario_positive_count}",
        f"scenario_negative_count={result.scenario_negative_count}",
        f"scenario_flat_count={result.scenario_flat_count}",
        f"baseline_positive_rate={result.baseline_positive_rate}",
        f"scenario_positive_rate={result.scenario_positive_rate}",
        f"positive_rate_delta={result.positive_rate_delta}",
        f"scenario_result={result.scenario_result}",
    ]


def batch_report(result: StrategyABBatchPerformanceResult) -> list[str]:
    lines = [
        "report_type=STRATEGY_AB_PERFORMANCE_BATCH",
        f"result_type={RESULT_TYPE}",
        f"requested_snapshot_count={result.requested_snapshot_count}",
        f"evaluated_snapshot_count={result.evaluated_snapshot_count}",
        f"successful_snapshot_count={result.successful_snapshot_count}",
        f"outcome_incomplete_count={result.outcome_incomplete_count}",
        (f"baseline_integrity_failed_count={result.baseline_integrity_failed_count}"),
        f"replay_incompatible_count={result.replay_incompatible_count}",
        f"invalid_outcome_count={result.invalid_outcome_count}",
        f"scenario_win_count={result.scenario_win_count}",
        f"scenario_loss_count={result.scenario_loss_count}",
        f"tie_count={result.tie_count}",
        f"scenario_win_rate={result.scenario_win_rate}",
        f"mean_baseline_return={result.mean_baseline_return}",
        f"mean_scenario_return={result.mean_scenario_return}",
        f"mean_return_delta={result.mean_return_delta}",
        f"median_snapshot_return_delta={result.median_snapshot_return_delta}",
        (f"mean_baseline_positive_rate={result.mean_baseline_positive_rate}"),
        (f"mean_scenario_positive_rate={result.mean_scenario_positive_rate}"),
    ]
    lines.extend(
        f"snapshot_id={item.snapshot_id} status={item.status} "
        f"mean_return_delta={item.mean_return_delta} "
        f"scenario_result={item.scenario_result}"
        for item in result.results
    )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = StrategyABPerformanceService(session)
    overrides = dict(namespace.override)
    if namespace.snapshot_id is not None:
        result = service.evaluate_snapshot(
            namespace.snapshot_id,
            horizon_minutes=namespace.horizon,
            overrides=overrides,
        )
        return snapshot_report(result), 0 if result.status == "SUCCESS" else 1
    result = service.evaluate_latest(
        namespace.latest if namespace.latest is not None else 1,
        horizon_minutes=namespace.horizon,
        overrides=overrides,
    )
    return batch_report(result), 0


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
        print(f"Strategy A/B performance rejected. reason={error}")
        return 2
    except Exception as error:
        print(f"Strategy A/B performance failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
