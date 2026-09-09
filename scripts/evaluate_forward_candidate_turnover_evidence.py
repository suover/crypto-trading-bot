import argparse
import json
from decimal import Decimal

from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
    INVALID_FORWARD_TURNOVER,
    RESULT_TYPE,
    ForwardCandidateTurnoverEvidenceResult,
    ForwardCandidateTurnoverEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


REPORT_TYPE = "FORWARD_CANDIDATE_TURNOVER_EVIDENCE"


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
        description=(
            "Evaluate TopN turnover for one registered candidate using only "
            "strictly post-registration stored snapshots."
        )
    )
    parser.add_argument("--candidate-id", type=_positive_integer, required=True)
    return parser.parse_args(args)


def _weights(weights: dict[str, Decimal]) -> str:
    return json.dumps(
        {
            name: "0" if value == 0 else format(value.normalize(), "f")
            for name, value in sorted(weights.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _ids(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def _summary(prefix: str, summary) -> list[str]:
    if summary is None:
        return [f"{prefix}_summary=None"]
    return [
        f"{prefix}_summary_transition_count={summary.transition_count}",
        f"{prefix}_total_entered_count={summary.total_entered_count}",
        f"{prefix}_total_exited_count={summary.total_exited_count}",
        f"{prefix}_mean_replacement_rate={summary.mean_replacement_rate}",
        f"{prefix}_median_replacement_rate={summary.median_replacement_rate}",
        f"{prefix}_min_replacement_rate={summary.min_replacement_rate}",
        f"{prefix}_max_replacement_rate={summary.max_replacement_rate}",
        f"{prefix}_mean_retention_rate={summary.mean_retention_rate}",
        f"{prefix}_median_retention_rate={summary.median_retention_rate}",
        (
            f"{prefix}_zero_replacement_transition_count="
            f"{summary.zero_replacement_transition_count}"
        ),
        (
            f"{prefix}_full_replacement_transition_count="
            f"{summary.full_replacement_transition_count}"
        ),
    ]


def report(result: ForwardCandidateTurnoverEvidenceResult) -> list[str]:
    candidate = result.candidate
    lines = [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        f"outcome_data_used={str(result.outcome_data_used).lower()}",
        f"cost_data_used={str(result.cost_data_used).lower()}",
        "automatic_policy_selection=false",
        f"policy_decision_performed={str(result.policy_decision_performed).lower()}",
        "promotion_performed=false",
        "shadow_policy_created=false",
        (
            "candidate_registration_verified="
            f"{str(result.candidate_registration_verified).lower()}"
        ),
        f"forward_anchor_enforced={str(result.forward_anchor_enforced).lower()}",
        (
            "pre_registration_snapshots_excluded="
            f"{str(result.pre_registration_snapshots_excluded).lower()}"
        ),
        (
            "pre_registration_transition_excluded="
            f"{str(result.pre_registration_transition_excluded).lower()}"
        ),
        (
            "forward_continuity_enforced="
            f"{str(result.forward_continuity_enforced).lower()}"
        ),
        (
            "first_forward_snapshot_has_no_prior_forward_transition="
            f"{str(result.first_forward_snapshot_has_no_prior_forward_transition).lower()}"
        ),
        f"candidate_id={result.candidate_id}",
        f"candidate_schema_version={getattr(candidate, 'candidate_schema_version', None)}",
        f"scenario_name={getattr(candidate, 'scenario_name', None)}",
        (
            "scenario_definition_signature="
            f"{getattr(candidate, 'scenario_definition_signature', None)}"
        ),
        (
            f"component_weights={_weights(candidate.component_weights)}"
            if candidate is not None
            else "component_weights=None"
        ),
        f"user_id={getattr(candidate, 'user_id', None)}",
        f"exchange={getattr(candidate, 'exchange', None)}",
        f"quote_asset={getattr(candidate, 'quote_asset', None)}",
        (
            "baseline_policy_signature="
            f"{getattr(candidate, 'baseline_policy_signature', None)}"
        ),
        f"effective_top_n={getattr(candidate, 'effective_top_n', None)}",
        f"reference_snapshot_id={getattr(candidate, 'reference_snapshot_id', None)}",
        (
            "reference_snapshot_captured_at="
            f"{getattr(candidate, 'reference_snapshot_captured_at', None)}"
        ),
        f"registered_at={getattr(candidate, 'registered_at', None)}",
        (
            "registration_snapshot_id_watermark="
            f"{getattr(candidate, 'registration_snapshot_id_watermark', None)}"
        ),
        (
            "registration_captured_at_watermark="
            f"{getattr(candidate, 'registration_captured_at_watermark', None)}"
        ),
        (
            "forward_timeline_snapshot_count="
            f"{len(result.forward_timeline_snapshot_ids)}"
        ),
        f"forward_timeline_snapshot_ids={_ids(result.forward_timeline_snapshot_ids)}",
        (
            "candidate_context_snapshot_count="
            f"{len(result.candidate_context_snapshot_ids)}"
        ),
        f"candidate_context_snapshot_ids={_ids(result.candidate_context_snapshot_ids)}",
        (
            "common_replayable_snapshot_count="
            f"{len(result.common_replayable_snapshot_ids)}"
        ),
        f"common_replayable_snapshot_ids={_ids(result.common_replayable_snapshot_ids)}",
        f"transition_count={result.transition_count}",
        f"continuity_break_count={result.continuity_break_count}",
        f"status={result.status}",
        f"safe_reason={result.safe_reason}",
    ]
    lines.extend(_summary("baseline", result.baseline_summary))
    lines.extend(_summary("candidate", result.candidate_summary))
    for transition in result.transitions:
        baseline = transition.baseline
        candidate_transition = transition.scenarios[0]
        candidate_selection = candidate_transition.transition
        lines.extend(
            (
                f"transition_index={transition.transition_index}",
                f"previous_snapshot_id={baseline.previous_snapshot_id}",
                f"current_snapshot_id={baseline.current_snapshot_id}",
                f"previous_captured_at={baseline.previous_captured_at}",
                f"current_captured_at={baseline.current_captured_at}",
                f"baseline_retained_count={baseline.retained_count}",
                f"baseline_entered_count={baseline.entered_count}",
                f"baseline_exited_count={baseline.exited_count}",
                f"baseline_retention_rate={baseline.retention_rate}",
                f"baseline_replacement_rate={baseline.replacement_rate}",
                f"baseline_previous_top_markets={','.join(baseline.previous_top_markets)}",
                f"baseline_current_top_markets={','.join(baseline.current_top_markets)}",
                f"candidate_retained_count={candidate_selection.retained_count}",
                f"candidate_entered_count={candidate_selection.entered_count}",
                f"candidate_exited_count={candidate_selection.exited_count}",
                f"candidate_retention_rate={candidate_selection.retention_rate}",
                f"candidate_replacement_rate={candidate_selection.replacement_rate}",
                (
                    "candidate_previous_top_markets="
                    f"{','.join(candidate_selection.previous_top_markets)}"
                ),
                (
                    "candidate_current_top_markets="
                    f"{','.join(candidate_selection.current_top_markets)}"
                ),
                (
                    "replacement_rate_delta_vs_baseline="
                    f"{candidate_transition.replacement_rate_delta_vs_baseline}"
                ),
            )
        )
    return lines


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    result = ForwardCandidateTurnoverEvidenceService(session).evaluate(
        candidate_id=namespace.candidate_id
    )
    return report(result), 1 if result.status == INVALID_FORWARD_TURNOVER else 0


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
        print(f"Forward candidate turnover evidence rejected. reason={error}")
        return 2
    except Exception as error:
        print(
            "Forward candidate turnover evidence failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
