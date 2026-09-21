from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    InvalidForwardCandidateProvenance,
    ValidatedForwardCandidate,
    aware_utc,
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    INVALID_TURNOVER_DATA,
    RankingSelectionTurnoverSummary,
    TemporalRankingTurnoverCohortResult,
    TemporalRankingTurnoverService,
    TemporalRankingTurnoverTransition,
)


RESULT_TYPE = "FORWARD_ONLY_TOP_N_SELECTION_TURNOVER_EVIDENCE"
SUCCESS = "SUCCESS"
NO_FORWARD_SNAPSHOTS = "NO_FORWARD_SNAPSHOTS"
INSUFFICIENT_FORWARD_TRANSITIONS = "INSUFFICIENT_FORWARD_TRANSITIONS"
NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS = "NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS"
INVALID_FORWARD_TURNOVER = "INVALID_FORWARD_TURNOVER"


class _InvalidForwardTurnover(Exception):
    pass


@dataclass(frozen=True)
class ForwardCandidateTurnoverEvidenceResult:
    candidate_id: int
    candidate: ForwardCandidateMetadata | None
    forward_timeline_snapshot_ids: tuple[int, ...]
    candidate_context_snapshot_ids: tuple[int, ...]
    common_replayable_snapshot_ids: tuple[int, ...]
    transition_count: int
    continuity_break_count: int
    baseline_summary: RankingSelectionTurnoverSummary | None
    candidate_summary: RankingSelectionTurnoverSummary | None
    transitions: tuple[TemporalRankingTurnoverTransition, ...]
    status: str
    safe_reason: str | None
    candidate_registration_verified: bool
    forward_anchor_enforced: bool
    pre_registration_snapshots_excluded: bool
    pre_registration_transition_excluded: bool
    forward_continuity_enforced: bool
    first_forward_snapshot_has_no_prior_forward_transition: bool
    outcome_data_used: bool
    cost_data_used: bool
    policy_decision_performed: bool


class ForwardCandidateTurnoverEvidenceService:
    """Compare turnover only across genuine adjacent post-registration snapshots."""

    def __init__(
        self,
        session: Session,
        *,
        turnover_service: TemporalRankingTurnoverService | None = None,
    ) -> None:
        self.session = session
        self.turnover_service = turnover_service or TemporalRankingTurnoverService(
            session
        )

    def evaluate(
        self, *, candidate_id: int, snapshot_id_ceiling: int | None = None
    ) -> ForwardCandidateTurnoverEvidenceResult:
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        try:
            validated = load_and_validate_forward_candidate(self.session, candidate_id)
            self._validate_ceiling(
                snapshot_id_ceiling,
                validated.metadata.registration_snapshot_id_watermark,
            )
            snapshots = self._load_forward_timeline(validated, snapshot_id_ceiling)
            self._validate_forward_timeline(validated, snapshots)
            if not snapshots:
                return self._safe_result(
                    validated,
                    (),
                    status=NO_FORWARD_SNAPSHOTS,
                    safe_reason="no snapshot satisfies the forward triple cutoff",
                )
            snapshot_ids = tuple(snapshot.id for snapshot in snapshots)
            turnover = self.turnover_service.evaluate_snapshots(
                scenarios=(validated.scenario,), snapshot_ids=snapshot_ids
            )
            if turnover.status == INVALID_TURNOVER_DATA:
                raise _InvalidForwardTurnover(
                    f"temporal turnover data is invalid: {turnover.safe_reason}"
                )
            self._validate_turnover_result(validated, snapshot_ids, turnover)
            matching = tuple(
                cohort
                for cohort in turnover.cohorts
                if (
                    cohort.baseline_policy_signature
                    == validated.metadata.baseline_policy_signature
                    and cohort.effective_top_n == validated.metadata.effective_top_n
                )
            )
            if len(matching) > 1:
                raise _InvalidForwardTurnover("candidate target cohort is duplicated")
            if not matching:
                return self._safe_result(
                    validated,
                    snapshot_ids,
                    status=NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS,
                    safe_reason=(
                        "forward timeline has no replayable snapshot in the "
                        "candidate context"
                    ),
                )
            cohort = matching[0]
            self._validate_target_cohort(validated, snapshot_ids, cohort)
            status = SUCCESS if cohort.transitions else INSUFFICIENT_FORWARD_TRANSITIONS
            return ForwardCandidateTurnoverEvidenceResult(
                candidate_id=candidate_id,
                candidate=validated.metadata,
                forward_timeline_snapshot_ids=snapshot_ids,
                candidate_context_snapshot_ids=cohort.candidate_snapshot_ids,
                common_replayable_snapshot_ids=(cohort.common_replayable_snapshot_ids),
                transition_count=cohort.transition_count,
                continuity_break_count=cohort.continuity_break_count,
                baseline_summary=cohort.baseline_summary,
                candidate_summary=cohort.scenario_summaries[0].summary,
                transitions=cohort.transitions,
                status=status,
                safe_reason=(
                    None
                    if status == SUCCESS
                    else "candidate cohort has no adjacent forward replayable pair"
                ),
                candidate_registration_verified=True,
                forward_anchor_enforced=True,
                pre_registration_snapshots_excluded=True,
                pre_registration_transition_excluded=True,
                forward_continuity_enforced=True,
                first_forward_snapshot_has_no_prior_forward_transition=True,
                outcome_data_used=False,
                cost_data_used=False,
                policy_decision_performed=False,
            )
        except (
            InvalidForwardCandidateProvenance,
            _InvalidForwardTurnover,
        ) as error:
            return self._invalid(candidate_id, str(error))

    def _load_forward_timeline(
        self,
        validated: ValidatedForwardCandidate,
        snapshot_id_ceiling: int | None = None,
    ) -> tuple[StrategyReplaySnapshot, ...]:
        candidate = validated.row
        query = select(StrategyReplaySnapshot).where(
            StrategyReplaySnapshot.user_id == candidate.user_id,
            StrategyReplaySnapshot.exchange == candidate.exchange,
            StrategyReplaySnapshot.quote_asset == candidate.quote_asset,
            StrategyReplaySnapshot.dataset_schema_version
            == candidate.dataset_schema_version,
            StrategyReplaySnapshot.id > candidate.registration_snapshot_id_watermark,
            StrategyReplaySnapshot.captured_at > candidate.registered_at,
            StrategyReplaySnapshot.captured_at
            > candidate.registration_captured_at_watermark,
        )
        if snapshot_id_ceiling is not None:
            query = query.where(StrategyReplaySnapshot.id <= snapshot_id_ceiling)
        return tuple(
            self.session.scalars(
                query.order_by(
                    StrategyReplaySnapshot.captured_at.asc(),
                    StrategyReplaySnapshot.id.asc(),
                ).execution_options(autoflush=False)
            )
        )

    @staticmethod
    def _validate_ceiling(value: int | None, watermark: int) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < watermark:
            raise ReplayInputError(
                "snapshot ID ceiling must be an integer at or above registration watermark"
            )

    @staticmethod
    def _validate_forward_timeline(
        validated: ValidatedForwardCandidate,
        snapshots: tuple[StrategyReplaySnapshot, ...],
    ) -> None:
        metadata = validated.metadata
        seen_ids: set[int] = set()
        previous_key: tuple[datetime, int] | None = None
        for snapshot in snapshots:
            if snapshot.id in seen_ids:
                raise _InvalidForwardTurnover("duplicate forward snapshot ID")
            seen_ids.add(snapshot.id)
            captured_at = aware_utc(snapshot.captured_at, "snapshot captured_at")
            key = (captured_at, snapshot.id)
            if previous_key is not None and key <= previous_key:
                raise _InvalidForwardTurnover("forward snapshot chronology is invalid")
            previous_key = key
            if (
                snapshot.id <= metadata.registration_snapshot_id_watermark
                or captured_at <= metadata.registered_at
                or captured_at <= metadata.registration_captured_at_watermark
            ):
                raise _InvalidForwardTurnover(
                    "forward snapshot violates the triple cutoff"
                )
            if (
                snapshot.user_id != metadata.user_id
                or snapshot.exchange != metadata.exchange
                or snapshot.quote_asset != metadata.quote_asset
                or snapshot.dataset_schema_version != metadata.dataset_schema_version
            ):
                raise _InvalidForwardTurnover("forward snapshot context mismatch")

    @staticmethod
    def _validate_turnover_result(validated, snapshot_ids, turnover) -> None:
        scenario = validated.scenario
        if (
            turnover.requested_snapshot_count != len(snapshot_ids)
            or turnover.replayed_snapshot_count != len(snapshot_ids)
            or turnover.scenario_count != 1
            or len(turnover.scenarios) != 1
            or turnover.scenarios[0].name != scenario.name
            or turnover.scenarios[0].definition_signature
            != scenario.definition_signature
            or turnover.scenarios[0].component_weights != scenario.component_weights
        ):
            raise _InvalidForwardTurnover("temporal turnover lineage mismatch")

    @staticmethod
    def _validate_target_cohort(
        validated: ValidatedForwardCandidate,
        snapshot_ids: tuple[int, ...],
        cohort: TemporalRankingTurnoverCohortResult,
    ) -> None:
        positions = {
            snapshot_id: index for index, snapshot_id in enumerate(snapshot_ids)
        }
        candidate_ids = cohort.candidate_snapshot_ids
        common_ids = cohort.common_replayable_snapshot_ids
        if (
            len(candidate_ids) != cohort.candidate_snapshot_count
            or len(common_ids) != cohort.common_replayable_snapshot_count
            or len(candidate_ids) != len(set(candidate_ids))
            or len(common_ids) != len(set(common_ids))
            or any(snapshot_id not in positions for snapshot_id in candidate_ids)
            or any(snapshot_id not in candidate_ids for snapshot_id in common_ids)
            or tuple(sorted(candidate_ids, key=positions.__getitem__)) != candidate_ids
            or tuple(sorted(common_ids, key=positions.__getitem__)) != common_ids
        ):
            raise _InvalidForwardTurnover("candidate cohort snapshot lineage mismatch")
        if len(cohort.scenario_summaries) != 1:
            raise _InvalidForwardTurnover("candidate scenario summary count mismatch")
        summary = cohort.scenario_summaries[0]
        if (
            summary.scenario_name != validated.metadata.scenario_name
            or summary.scenario_definition_signature
            != validated.metadata.scenario_definition_signature
            or not summary.scenario_signature
            or cohort.transition_count != len(cohort.transitions)
            or cohort.baseline_summary.transition_count != cohort.transition_count
            or summary.summary.transition_count != cohort.transition_count
        ):
            raise _InvalidForwardTurnover("candidate scenario identity mismatch")
        for expected_index, transition in enumerate(cohort.transitions, start=1):
            if len(transition.scenarios) != 1:
                raise _InvalidForwardTurnover(
                    "candidate transition scenario count mismatch"
                )
            scenario = transition.scenarios[0]
            baseline = transition.baseline
            candidate_transition = scenario.transition
            if (
                transition.transition_index != expected_index
                or scenario.scenario_name != summary.scenario_name
                or scenario.scenario_signature != summary.scenario_signature
                or baseline.effective_top_n != validated.metadata.effective_top_n
                or candidate_transition.effective_top_n
                != validated.metadata.effective_top_n
                or baseline.previous_snapshot_id
                != candidate_transition.previous_snapshot_id
                or baseline.current_snapshot_id
                != candidate_transition.current_snapshot_id
                or baseline.previous_captured_at
                != candidate_transition.previous_captured_at
                or baseline.current_captured_at
                != candidate_transition.current_captured_at
                or baseline.previous_snapshot_id not in common_ids
                or baseline.current_snapshot_id not in common_ids
                or positions[baseline.current_snapshot_id]
                != positions[baseline.previous_snapshot_id] + 1
                or scenario.replacement_rate_delta_vs_baseline
                != candidate_transition.replacement_rate - baseline.replacement_rate
            ):
                raise _InvalidForwardTurnover(
                    "candidate transition scenario identity mismatch"
                )

    @staticmethod
    def _safe_result(
        validated: ValidatedForwardCandidate,
        snapshot_ids: tuple[int, ...],
        *,
        status: str,
        safe_reason: str,
    ) -> ForwardCandidateTurnoverEvidenceResult:
        return ForwardCandidateTurnoverEvidenceResult(
            candidate_id=validated.metadata.candidate_id,
            candidate=validated.metadata,
            forward_timeline_snapshot_ids=snapshot_ids,
            candidate_context_snapshot_ids=(),
            common_replayable_snapshot_ids=(),
            transition_count=0,
            continuity_break_count=0,
            baseline_summary=None,
            candidate_summary=None,
            transitions=(),
            status=status,
            safe_reason=safe_reason,
            candidate_registration_verified=True,
            forward_anchor_enforced=True,
            pre_registration_snapshots_excluded=True,
            pre_registration_transition_excluded=True,
            forward_continuity_enforced=True,
            first_forward_snapshot_has_no_prior_forward_transition=True,
            outcome_data_used=False,
            cost_data_used=False,
            policy_decision_performed=False,
        )

    @staticmethod
    def _invalid(
        candidate_id: int, reason: str
    ) -> ForwardCandidateTurnoverEvidenceResult:
        return ForwardCandidateTurnoverEvidenceResult(
            candidate_id=candidate_id,
            candidate=None,
            forward_timeline_snapshot_ids=(),
            candidate_context_snapshot_ids=(),
            common_replayable_snapshot_ids=(),
            transition_count=0,
            continuity_break_count=0,
            baseline_summary=None,
            candidate_summary=None,
            transitions=(),
            status=INVALID_FORWARD_TURNOVER,
            safe_reason=reason,
            candidate_registration_verified=False,
            forward_anchor_enforced=False,
            pre_registration_snapshots_excluded=False,
            pre_registration_transition_excluded=False,
            forward_continuity_enforced=False,
            first_forward_snapshot_has_no_prior_forward_transition=False,
            outcome_data_used=False,
            cost_data_used=False,
            policy_decision_performed=False,
        )


__all__ = [
    "INSUFFICIENT_FORWARD_TRANSITIONS",
    "INVALID_FORWARD_TURNOVER",
    "NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS",
    "NO_FORWARD_SNAPSHOTS",
    "RESULT_TYPE",
    "SUCCESS",
    "ForwardCandidateTurnoverEvidenceResult",
    "ForwardCandidateTurnoverEvidenceService",
]
