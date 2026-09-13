from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from typing import Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ShadowPolicyEvaluation,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    CostAssumptions,
    SelectionChangeCost,
    build_cost_assumptions,
    compute_selection_change_cost,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    InvalidForwardCandidateProvenance,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentError,
)
from crypto_trading_bot.services.shadow_policy_evaluation_service import (
    BASELINE_INTEGRITY_FAILED,
    CONTEXT_MISMATCH,
    REPLAY_INCOMPATIBLE,
    SUCCESS as SELECTION_SUCCESS,
    ShadowPolicyEvaluationError,
    validate_stored_shadow_policy_evaluation,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    ValidatedShadowPolicyEnrollment,
    load_and_validate_shadow_policy_enrollment,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    SUCCESS as GROSS_SNAPSHOT_SUCCESS,
    StoredSelectionPerformanceInput,
    StrategyABBatchPerformanceResult,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    RankingSelectionTransition,
    RankingSelectionTurnoverSummary,
    build_ranking_selection_transition,
    summarize_ranking_selection_transitions,
)


RESULT_TYPE = "SHADOW_POLICY_PERFORMANCE_EVIDENCE_V1"
SUCCESS = "SUCCESS"
NO_SHADOW_ENROLLMENT = "NO_SHADOW_ENROLLMENT"
NO_SHADOW_EVALUATIONS = "NO_SHADOW_EVALUATIONS"
INVALID_SHADOW_PERFORMANCE_EVIDENCE = "INVALID_SHADOW_PERFORMANCE_EVIDENCE"
NO_SUCCESSFUL_SHADOW_SELECTIONS = "NO_SUCCESSFUL_SHADOW_SELECTIONS"
SHADOW_OUTCOMES_PENDING = "SHADOW_OUTCOMES_PENDING"
INVALID_SHADOW_OUTCOME_DATA = "INVALID_SHADOW_OUTCOME_DATA"
INSUFFICIENT_SHADOW_TRANSITIONS = "INSUFFICIENT_SHADOW_TRANSITIONS"
NO_SHADOW_COST_ADJUSTABLE_SNAPSHOTS = "NO_SHADOW_COST_ADJUSTABLE_SNAPSHOTS"


class _InvalidShadowPerformanceEvidence(Exception):
    pass


@dataclass(frozen=True)
class ShadowGrossHorizonEvidence:
    horizon_minutes: int
    eligible_shadow_snapshot_count: int
    successful_comparable_snapshot_count: int
    outcome_incomplete_count: int
    invalid_outcome_count: int
    shadow_win_count: int
    shadow_loss_count: int
    tie_count: int
    shadow_win_rate: Decimal | None
    mean_baseline_return: Decimal | None
    mean_shadow_return: Decimal | None
    mean_return_delta: Decimal | None
    median_snapshot_return_delta: Decimal | None
    mean_baseline_positive_rate: Decimal | None
    mean_shadow_positive_rate: Decimal | None
    status: str
    safe_reason: str | None
    snapshots: tuple[StrategyABSnapshotPerformanceResult, ...]


@dataclass(frozen=True)
class ShadowTurnoverTransitionEvidence:
    previous_snapshot_id: int
    current_snapshot_id: int
    previous_captured_at: datetime
    current_captured_at: datetime
    baseline: RankingSelectionTransition
    shadow: RankingSelectionTransition
    replacement_rate_delta_vs_baseline: Decimal


@dataclass(frozen=True)
class ShadowTurnoverEvidence:
    timeline_snapshot_ids: tuple[int, ...]
    candidate_context_snapshot_ids: tuple[int, ...]
    successful_selection_snapshot_ids: tuple[int, ...]
    transition_count: int
    continuity_break_count: int
    baseline_summary: RankingSelectionTurnoverSummary
    shadow_summary: RankingSelectionTurnoverSummary
    mean_replacement_rate_delta_vs_baseline: Decimal | None
    status: str
    safe_reason: str | None
    transitions: tuple[ShadowTurnoverTransitionEvidence, ...]


@dataclass(frozen=True)
class ShadowCostAdjustedSnapshotEvidence:
    snapshot_id: int
    previous_snapshot_id: int
    pipeline_run_id: str
    captured_at: datetime
    horizon_minutes: int
    candidate_id: int
    shadow_enrollment_id: int
    baseline_policy_signature: str
    effective_top_n: int
    scenario_name: str
    scenario_definition_signature: str
    scenario_signature: str
    fee_rate: Decimal
    spread_cost_rate: Decimal
    slippage_rate: Decimal
    total_cost_rate: Decimal
    target_weight: Decimal
    baseline_replacement_rate: Decimal
    shadow_replacement_rate: Decimal
    baseline_execution_cost_percentage: Decimal
    shadow_execution_cost_percentage: Decimal
    baseline_gross_return: Decimal
    shadow_gross_return: Decimal
    gross_return_delta: Decimal
    baseline_cost_adjusted_return: Decimal
    shadow_cost_adjusted_return: Decimal
    cost_adjusted_return_delta: Decimal
    cost_adjusted_shadow_result: str


@dataclass(frozen=True)
class ShadowCostAdjustedHorizonEvidence:
    horizon_minutes: int
    eligible_shadow_snapshot_count: int
    successful_gross_snapshot_count: int
    shadow_transition_count: int
    cost_adjustable_shadow_snapshot_count: int
    cost_adjustable_shadow_snapshot_ids: tuple[int, ...]
    cost_adjustable_coverage_rate: Decimal | None
    outcome_incomplete_count: int
    mean_baseline_gross_return: Decimal | None
    mean_shadow_gross_return: Decimal | None
    mean_gross_return_delta: Decimal | None
    mean_baseline_replacement_rate: Decimal | None
    mean_shadow_replacement_rate: Decimal | None
    mean_baseline_execution_cost_percentage: Decimal | None
    mean_shadow_execution_cost_percentage: Decimal | None
    mean_execution_cost_delta_percentage: Decimal | None
    mean_baseline_cost_adjusted_return: Decimal | None
    mean_shadow_cost_adjusted_return: Decimal | None
    mean_cost_adjusted_return_delta: Decimal | None
    median_cost_adjusted_return_delta: Decimal | None
    cost_adjusted_shadow_win_count: int
    cost_adjusted_shadow_loss_count: int
    tie_count: int
    cost_adjusted_shadow_win_rate: Decimal | None
    status: str
    safe_reason: str | None
    snapshots: tuple[ShadowCostAdjustedSnapshotEvidence, ...]


@dataclass(frozen=True)
class ShadowPolicyPerformanceResult:
    result_type: str
    candidate_id: int
    enrollment: object | None
    performance_evidence_as_of: datetime
    shadow_evaluation_snapshot_id_ceiling: int | None
    requested_horizons: tuple[int, ...]
    cost_assumptions: CostAssumptions
    timeline_snapshot_ids: tuple[int, ...]
    candidate_context_snapshot_ids: tuple[int, ...]
    successful_selection_snapshot_ids: tuple[int, ...]
    timeline_evaluation_count: int
    successful_selection_count: int
    context_mismatch_count: int
    baseline_integrity_failed_count: int
    replay_incompatible_count: int
    first_success_captured_at: datetime | None
    last_success_captured_at: datetime | None
    observation_span_hours: Decimal | None
    gross: tuple[ShadowGrossHorizonEvidence, ...]
    turnover: ShadowTurnoverEvidence | None
    cost_adjusted: tuple[ShadowCostAdjustedHorizonEvidence, ...]
    status: str
    safe_reason: str | None
    shadow_enrollment_verified: bool
    shadow_boundary_enforced: bool
    performance_as_of_enforced: bool
    evaluation_ceiling_enforced: bool
    stored_shadow_selection_reused: bool
    offline_replay_performed: bool
    outcome_data_used: bool
    performance_evaluated: bool
    cost_model_reused: bool
    turnover_continuity_enforced: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    sample_sufficiency_assessed: bool
    statistical_inference_performed: bool
    policy_decision_performed: bool
    promotion_performed: bool
    shadow_runtime_changed: bool


class ShadowPolicyPerformanceService:
    """Derive read-only performance from immutable stored Shadow selections."""

    def __init__(
        self,
        session: Session,
        *,
        performance_service: StrategyABPerformanceService | None = None,
        now_fn: Callable[[], datetime] | None = None,
        after_ceiling_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.performance_service = performance_service or StrategyABPerformanceService(
            session
        )
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.after_ceiling_fn = after_ceiling_fn

    def evaluate(
        self,
        *,
        candidate_id: int,
        horizons: Iterable[int],
        fee_rate: object,
        spread_cost_rate: object,
        slippage_rate: object,
    ) -> ShadowPolicyPerformanceResult:
        normalized_horizons = self._normalize_horizons(horizons)
        assumptions = build_cost_assumptions(
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        as_of = self._aware_utc(self.now_fn(), "performance evidence clock")
        try:
            validated = load_and_validate_shadow_policy_enrollment(
                self.session, candidate_id
            )
            if validated is None:
                return self._safe_result(
                    candidate_id,
                    normalized_horizons,
                    assumptions,
                    as_of,
                    NO_SHADOW_ENROLLMENT,
                    "candidate has no Shadow enrollment",
                )
            ceiling = self._capture_ceiling(validated, as_of)
            if ceiling is None:
                return self._safe_result(
                    candidate_id,
                    normalized_horizons,
                    assumptions,
                    as_of,
                    NO_SHADOW_EVALUATIONS,
                    "no Shadow evaluation is knowable at the performance as-of",
                    enrollment=validated.row,
                    enrollment_verified=True,
                )
            if self.after_ceiling_fn is not None:
                self.after_ceiling_fn()
            timeline = self._load_timeline(validated, as_of, ceiling)
            self._validate_timeline(validated, timeline, as_of, ceiling)
            successful = tuple(
                row for row, _ in timeline if row.evaluation_status == SELECTION_SUCCESS
            )
            selections = tuple(self._stored_input(row) for row in successful)
            gross = tuple(
                self._gross_horizon(selections, horizon=horizon, outcome_as_of=as_of)
                for horizon in normalized_horizons
            )
            if any(item.invalid_outcome_count for item in gross):
                raise _InvalidShadowPerformanceEvidence(
                    "stored candidate outcome lineage or value is invalid"
                )
            turnover = self._turnover(timeline)
            cost_adjusted = tuple(
                self._cost_horizon(
                    validated,
                    gross_horizon,
                    turnover,
                    assumptions,
                )
                for gross_horizon in gross
            )
            success_times = tuple(
                self._aware_utc(row.snapshot_captured_at, "snapshot captured_at")
                for row in successful
            )
            span = (
                Decimal(str((success_times[-1] - success_times[0]).total_seconds()))
                / Decimal("3600")
                if len(success_times) >= 2
                else None
            )
            return ShadowPolicyPerformanceResult(
                result_type=RESULT_TYPE,
                candidate_id=candidate_id,
                enrollment=validated.row,
                performance_evidence_as_of=as_of,
                shadow_evaluation_snapshot_id_ceiling=ceiling,
                requested_horizons=normalized_horizons,
                cost_assumptions=assumptions,
                timeline_snapshot_ids=tuple(
                    row.strategy_replay_snapshot_id for row, _ in timeline
                ),
                candidate_context_snapshot_ids=tuple(
                    row.strategy_replay_snapshot_id
                    for row, _ in timeline
                    if row.context_matches_enrollment
                ),
                successful_selection_snapshot_ids=tuple(
                    row.strategy_replay_snapshot_id for row in successful
                ),
                timeline_evaluation_count=len(timeline),
                successful_selection_count=len(successful),
                context_mismatch_count=sum(
                    row.evaluation_status == CONTEXT_MISMATCH for row, _ in timeline
                ),
                baseline_integrity_failed_count=sum(
                    row.evaluation_status == BASELINE_INTEGRITY_FAILED
                    for row, _ in timeline
                ),
                replay_incompatible_count=sum(
                    row.evaluation_status == REPLAY_INCOMPATIBLE for row, _ in timeline
                ),
                first_success_captured_at=success_times[0] if success_times else None,
                last_success_captured_at=success_times[-1] if success_times else None,
                observation_span_hours=span,
                gross=gross,
                turnover=turnover,
                cost_adjusted=cost_adjusted,
                status=SUCCESS,
                safe_reason=None,
                shadow_enrollment_verified=True,
                shadow_boundary_enforced=True,
                performance_as_of_enforced=True,
                evaluation_ceiling_enforced=True,
                stored_shadow_selection_reused=True,
                offline_replay_performed=False,
                outcome_data_used=bool(successful),
                performance_evaluated=True,
                cost_model_reused=True,
                turnover_continuity_enforced=True,
                database_write=False,
                external_calls=False,
                live_policy_change=False,
                sample_sufficiency_assessed=False,
                statistical_inference_performed=False,
                policy_decision_performed=False,
                promotion_performed=False,
                shadow_runtime_changed=False,
            )
        except (
            _InvalidShadowPerformanceEvidence,
            InvalidForwardCandidateProvenance,
            ShadowPolicyEnrollmentError,
            ShadowPolicyEvaluationError,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            return self._safe_result(
                candidate_id,
                normalized_horizons,
                assumptions,
                as_of,
                INVALID_SHADOW_PERFORMANCE_EVIDENCE,
                str(error),
            )

    def _capture_ceiling(
        self, validated: ValidatedShadowPolicyEnrollment, as_of: datetime
    ) -> int | None:
        return self.session.scalar(
            select(func.max(ShadowPolicyEvaluation.strategy_replay_snapshot_id))
            .where(*self._evaluation_predicates(validated, as_of))
            .execution_options(autoflush=False)
        )

    def _load_timeline(self, validated, as_of, ceiling):
        return tuple(
            self.session.execute(
                select(ShadowPolicyEvaluation, StrategyReplaySnapshot)
                .join(
                    StrategyReplaySnapshot,
                    StrategyReplaySnapshot.id
                    == ShadowPolicyEvaluation.strategy_replay_snapshot_id,
                )
                .where(
                    *self._evaluation_predicates(validated, as_of),
                    ShadowPolicyEvaluation.strategy_replay_snapshot_id <= ceiling,
                )
                .order_by(
                    ShadowPolicyEvaluation.snapshot_captured_at,
                    ShadowPolicyEvaluation.strategy_replay_snapshot_id,
                )
                .execution_options(autoflush=False)
            )
        )

    @staticmethod
    def _evaluation_predicates(validated, as_of):
        return (
            ShadowPolicyEvaluation.shadow_enrollment_id == validated.row.id,
            ShadowPolicyEvaluation.evaluated_at <= as_of,
            ShadowPolicyEvaluation.created_at <= as_of,
            ShadowPolicyEvaluation.snapshot_captured_at <= as_of,
        )

    def _validate_timeline(self, validated, timeline, as_of, ceiling):
        seen: set[int] = set()
        previous_key: tuple[datetime, int] | None = None
        for row, snapshot in timeline:
            captured_at = self._aware_utc(
                row.snapshot_captured_at, "snapshot captured_at"
            )
            key = (captured_at, row.strategy_replay_snapshot_id)
            if row.strategy_replay_snapshot_id in seen or (
                previous_key is not None and key <= previous_key
            ):
                raise _InvalidShadowPerformanceEvidence(
                    "Shadow evaluation chronology is invalid"
                )
            seen.add(row.strategy_replay_snapshot_id)
            previous_key = key
            if (
                row.strategy_replay_snapshot_id > ceiling
                or self._aware_utc(row.evaluated_at, "evaluation evaluated_at") > as_of
                or self._aware_utc(row.created_at, "evaluation created_at") > as_of
                or captured_at > as_of
            ):
                raise _InvalidShadowPerformanceEvidence(
                    "Shadow evaluation violates the frozen as-of or ceiling"
                )
            validate_stored_shadow_policy_evaluation(row, snapshot, validated)

    @staticmethod
    def _stored_input(row) -> StoredSelectionPerformanceInput:
        return StoredSelectionPerformanceInput(
            snapshot_id=row.strategy_replay_snapshot_id,
            pipeline_run_id=row.pipeline_run_id,
            captured_at=row.snapshot_captured_at,
            baseline_policy_signature=row.baseline_policy_signature,
            scenario_signature=row.scenario_signature,
            replay_status=row.replay_status,
            replay_safe_reason=row.safe_reason,
            baseline_matches_stored=row.baseline_matches_stored,
            effective_top_n=row.effective_top_n,
            baseline_top_markets=tuple(row.baseline_top_markets),
            scenario_top_markets=tuple(row.shadow_top_markets),
            top_n_overlap_count=row.top_n_overlap_count,
            top_n_overlap_rate=row.top_n_overlap_rate,
            entered_top_n=tuple(row.entered_top_n),
            exited_top_n=tuple(row.exited_top_n),
        )

    def _gross_horizon(self, selections, *, horizon, outcome_as_of):
        rows = self.performance_service.evaluate_stored_selections(
            selections,
            horizon_minutes=horizon,
            outcome_as_of=outcome_as_of,
        )
        aggregate = self.performance_service.summarize_results(len(rows), rows)
        status, reason = self._gross_status(len(selections), aggregate)
        return ShadowGrossHorizonEvidence(
            horizon_minutes=horizon,
            eligible_shadow_snapshot_count=len(selections),
            successful_comparable_snapshot_count=aggregate.successful_snapshot_count,
            outcome_incomplete_count=aggregate.outcome_incomplete_count,
            invalid_outcome_count=aggregate.invalid_outcome_count,
            shadow_win_count=aggregate.scenario_win_count,
            shadow_loss_count=aggregate.scenario_loss_count,
            tie_count=aggregate.tie_count,
            shadow_win_rate=aggregate.scenario_win_rate,
            mean_baseline_return=aggregate.mean_baseline_return,
            mean_shadow_return=aggregate.mean_scenario_return,
            mean_return_delta=aggregate.mean_return_delta,
            median_snapshot_return_delta=aggregate.median_snapshot_return_delta,
            mean_baseline_positive_rate=aggregate.mean_baseline_positive_rate,
            mean_shadow_positive_rate=aggregate.mean_scenario_positive_rate,
            status=status,
            safe_reason=reason,
            snapshots=rows,
        )

    @staticmethod
    def _gross_status(eligible, aggregate: StrategyABBatchPerformanceResult):
        if eligible == 0:
            return NO_SUCCESSFUL_SHADOW_SELECTIONS, "no successful Shadow selections"
        if aggregate.invalid_outcome_count:
            return INVALID_SHADOW_OUTCOME_DATA, "selected outcome data is invalid"
        if aggregate.successful_snapshot_count:
            return SUCCESS, None
        return SHADOW_OUTCOMES_PENDING, "Shadow outcomes are incomplete"

    def _turnover(self, timeline) -> ShadowTurnoverEvidence:
        transitions: list[ShadowTurnoverTransitionEvidence] = []
        breaks = 0
        for previous, current in zip(timeline, timeline[1:], strict=False):
            previous_row, _ = previous
            current_row, _ = current
            valid = (
                previous_row.evaluation_status == SELECTION_SUCCESS
                and current_row.evaluation_status == SELECTION_SUCCESS
            )
            if not valid and (
                previous_row.context_matches_enrollment
                or current_row.context_matches_enrollment
            ):
                breaks += 1
            if not valid:
                continue
            baseline = self._selection_transition(
                previous_row, current_row, use_shadow=False
            )
            shadow = self._selection_transition(
                previous_row, current_row, use_shadow=True
            )
            transitions.append(
                ShadowTurnoverTransitionEvidence(
                    previous_snapshot_id=previous_row.strategy_replay_snapshot_id,
                    current_snapshot_id=current_row.strategy_replay_snapshot_id,
                    previous_captured_at=baseline.previous_captured_at,
                    current_captured_at=baseline.current_captured_at,
                    baseline=baseline,
                    shadow=shadow,
                    replacement_rate_delta_vs_baseline=(
                        shadow.replacement_rate - baseline.replacement_rate
                    ),
                )
            )
        baseline_summary = summarize_ranking_selection_transitions(
            tuple(item.baseline for item in transitions)
        )
        shadow_summary = summarize_ranking_selection_transitions(
            tuple(item.shadow for item in transitions)
        )
        deltas = tuple(item.replacement_rate_delta_vs_baseline for item in transitions)
        return ShadowTurnoverEvidence(
            timeline_snapshot_ids=tuple(
                row.strategy_replay_snapshot_id for row, _ in timeline
            ),
            candidate_context_snapshot_ids=tuple(
                row.strategy_replay_snapshot_id
                for row, _ in timeline
                if row.context_matches_enrollment
            ),
            successful_selection_snapshot_ids=tuple(
                row.strategy_replay_snapshot_id
                for row, _ in timeline
                if row.evaluation_status == SELECTION_SUCCESS
            ),
            transition_count=len(transitions),
            continuity_break_count=breaks,
            baseline_summary=baseline_summary,
            shadow_summary=shadow_summary,
            mean_replacement_rate_delta_vs_baseline=(
                self._mean(deltas) if deltas else None
            ),
            status=SUCCESS if transitions else INSUFFICIENT_SHADOW_TRANSITIONS,
            safe_reason=None if transitions else "no adjacent successful Shadow pair",
            transitions=tuple(transitions),
        )

    @staticmethod
    def _selection_transition(previous, current, *, use_shadow):
        return build_ranking_selection_transition(
            previous_snapshot_id=previous.strategy_replay_snapshot_id,
            current_snapshot_id=current.strategy_replay_snapshot_id,
            previous_captured_at=previous.snapshot_captured_at,
            current_captured_at=current.snapshot_captured_at,
            effective_top_n=current.effective_top_n,
            previous_top_markets=tuple(
                previous.shadow_top_markets
                if use_shadow
                else previous.baseline_top_markets
            ),
            current_top_markets=tuple(
                current.shadow_top_markets
                if use_shadow
                else current.baseline_top_markets
            ),
        )

    def _cost_horizon(self, validated, gross, turnover, assumptions):
        gross_by_id = {row.snapshot_id: row for row in gross.snapshots}
        transitions = {row.current_snapshot_id: row for row in turnover.transitions}
        values: list[ShadowCostAdjustedSnapshotEvidence] = []
        for snapshot_id in gross_by_id.keys() & transitions.keys():
            row = gross_by_id[snapshot_id]
            if row.status != GROSS_SNAPSHOT_SUCCESS:
                continue
            values.append(
                self._cost_snapshot(
                    validated, row, transitions[snapshot_id], assumptions
                )
            )
        values.sort(key=lambda item: (item.captured_at, item.snapshot_id))
        snapshots = tuple(values)
        eligible = gross.eligible_shadow_snapshot_count
        status, reason = self._cost_status(gross, turnover, snapshots)
        return ShadowCostAdjustedHorizonEvidence(
            horizon_minutes=gross.horizon_minutes,
            eligible_shadow_snapshot_count=eligible,
            successful_gross_snapshot_count=gross.successful_comparable_snapshot_count,
            shadow_transition_count=turnover.transition_count,
            cost_adjustable_shadow_snapshot_count=len(snapshots),
            cost_adjustable_shadow_snapshot_ids=tuple(
                row.snapshot_id for row in snapshots
            ),
            cost_adjustable_coverage_rate=(
                Decimal(len(snapshots)) / Decimal(eligible) if eligible else None
            ),
            outcome_incomplete_count=gross.outcome_incomplete_count,
            mean_baseline_gross_return=self._mean(
                tuple(row.baseline_gross_return for row in snapshots)
            ),
            mean_shadow_gross_return=self._mean(
                tuple(row.shadow_gross_return for row in snapshots)
            ),
            mean_gross_return_delta=self._mean(
                tuple(row.gross_return_delta for row in snapshots)
            ),
            mean_baseline_replacement_rate=self._mean(
                tuple(row.baseline_replacement_rate for row in snapshots)
            ),
            mean_shadow_replacement_rate=self._mean(
                tuple(row.shadow_replacement_rate for row in snapshots)
            ),
            mean_baseline_execution_cost_percentage=self._mean(
                tuple(row.baseline_execution_cost_percentage for row in snapshots)
            ),
            mean_shadow_execution_cost_percentage=self._mean(
                tuple(row.shadow_execution_cost_percentage for row in snapshots)
            ),
            mean_execution_cost_delta_percentage=self._mean(
                tuple(
                    row.shadow_execution_cost_percentage
                    - row.baseline_execution_cost_percentage
                    for row in snapshots
                )
            ),
            mean_baseline_cost_adjusted_return=self._mean(
                tuple(row.baseline_cost_adjusted_return for row in snapshots)
            ),
            mean_shadow_cost_adjusted_return=self._mean(
                tuple(row.shadow_cost_adjusted_return for row in snapshots)
            ),
            mean_cost_adjusted_return_delta=self._mean(
                tuple(row.cost_adjusted_return_delta for row in snapshots)
            ),
            median_cost_adjusted_return_delta=(
                median(tuple(row.cost_adjusted_return_delta for row in snapshots))
                if snapshots
                else None
            ),
            cost_adjusted_shadow_win_count=sum(
                row.cost_adjusted_return_delta > 0 for row in snapshots
            ),
            cost_adjusted_shadow_loss_count=sum(
                row.cost_adjusted_return_delta < 0 for row in snapshots
            ),
            tie_count=sum(row.cost_adjusted_return_delta == 0 for row in snapshots),
            cost_adjusted_shadow_win_rate=(
                Decimal(sum(row.cost_adjusted_return_delta > 0 for row in snapshots))
                / Decimal(len(snapshots))
                if snapshots
                else None
            ),
            status=status,
            safe_reason=reason,
            snapshots=snapshots,
        )

    @staticmethod
    def _cost_status(gross, turnover, snapshots):
        if snapshots:
            return SUCCESS, None
        if turnover.transition_count == 0:
            return INSUFFICIENT_SHADOW_TRANSITIONS, "no valid Shadow transition"
        if gross.status == SHADOW_OUTCOMES_PENDING:
            return SHADOW_OUTCOMES_PENDING, "Shadow outcomes are incomplete"
        return (
            NO_SHADOW_COST_ADJUSTABLE_SNAPSHOTS,
            "Gross SUCCESS and turnover current snapshots do not overlap",
        )

    @staticmethod
    def _cost_snapshot(validated, gross, transition, assumptions):
        if (
            gross.snapshot_id != transition.current_snapshot_id
            or gross.captured_at.astimezone(UTC) != transition.current_captured_at
            or gross.effective_top_n != transition.baseline.effective_top_n
            or gross.baseline_top_markets != transition.baseline.current_top_markets
            or gross.scenario_top_markets != transition.shadow.current_top_markets
        ):
            raise _InvalidShadowPerformanceEvidence(
                "Gross/turnover Shadow lineage mismatch"
            )
        baseline_cost = compute_selection_change_cost(transition.baseline, assumptions)
        shadow_cost = compute_selection_change_cost(transition.shadow, assumptions)
        baseline_return = ShadowPolicyPerformanceService._required_decimal(
            gross.baseline_mean_return, "baseline gross return"
        )
        shadow_return = ShadowPolicyPerformanceService._required_decimal(
            gross.scenario_mean_return, "shadow gross return"
        )
        gross_delta = ShadowPolicyPerformanceService._required_decimal(
            gross.mean_return_delta, "gross return delta"
        )
        baseline_adjusted = baseline_return - baseline_cost.execution_cost_percentage
        shadow_adjusted = shadow_return - shadow_cost.execution_cost_percentage
        adjusted_delta = shadow_adjusted - baseline_adjusted
        enrollment = validated.row
        return ShadowCostAdjustedSnapshotEvidence(
            snapshot_id=gross.snapshot_id,
            previous_snapshot_id=transition.previous_snapshot_id,
            pipeline_run_id=gross.pipeline_run_id,
            captured_at=gross.captured_at.astimezone(UTC),
            horizon_minutes=gross.horizon_minutes,
            candidate_id=enrollment.candidate_id,
            shadow_enrollment_id=enrollment.id,
            baseline_policy_signature=enrollment.baseline_policy_signature,
            effective_top_n=enrollment.effective_top_n,
            scenario_name=enrollment.scenario_name,
            scenario_definition_signature=enrollment.scenario_definition_signature,
            scenario_signature=gross.scenario_signature,
            fee_rate=assumptions.fee_rate,
            spread_cost_rate=assumptions.spread_cost_rate,
            slippage_rate=assumptions.slippage_rate,
            total_cost_rate=assumptions.total_cost_rate,
            target_weight=ShadowPolicyPerformanceService._same_target_weight(
                baseline_cost, shadow_cost
            ),
            baseline_replacement_rate=baseline_cost.replacement_rate,
            shadow_replacement_rate=shadow_cost.replacement_rate,
            baseline_execution_cost_percentage=baseline_cost.execution_cost_percentage,
            shadow_execution_cost_percentage=shadow_cost.execution_cost_percentage,
            baseline_gross_return=baseline_return,
            shadow_gross_return=shadow_return,
            gross_return_delta=gross_delta,
            baseline_cost_adjusted_return=baseline_adjusted,
            shadow_cost_adjusted_return=shadow_adjusted,
            cost_adjusted_return_delta=adjusted_delta,
            cost_adjusted_shadow_result=(
                "SHADOW_WIN"
                if adjusted_delta > 0
                else "SHADOW_LOSS"
                if adjusted_delta < 0
                else "TIE"
            ),
        )

    @staticmethod
    def _same_target_weight(
        baseline: SelectionChangeCost, shadow: SelectionChangeCost
    ) -> Decimal:
        if baseline.target_weight != shadow.target_weight:
            raise _InvalidShadowPerformanceEvidence("cost target weights differ")
        return baseline.target_weight

    @staticmethod
    def _required_decimal(value, field_name):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise _InvalidShadowPerformanceEvidence(f"{field_name} is invalid")
        return value

    @staticmethod
    def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
        return sum(values, Decimal("0")) / Decimal(len(values)) if values else None

    @staticmethod
    def _normalize_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
        values = tuple(horizons)
        if not values or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in values
        ):
            raise ReplayInputError("horizons must contain positive integers")
        if len(values) != len(set(values)):
            raise ReplayInputError("horizons must not contain duplicates")
        return tuple(sorted(values))

    @staticmethod
    def _aware_utc(value: object, field_name: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ReplayInputError(f"{field_name} must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _safe_result(
        candidate_id,
        horizons,
        assumptions,
        as_of,
        status,
        reason,
        *,
        enrollment=None,
        enrollment_verified=False,
    ):
        return ShadowPolicyPerformanceResult(
            result_type=RESULT_TYPE,
            candidate_id=candidate_id,
            enrollment=enrollment,
            performance_evidence_as_of=as_of,
            shadow_evaluation_snapshot_id_ceiling=None,
            requested_horizons=horizons,
            cost_assumptions=assumptions,
            timeline_snapshot_ids=(),
            candidate_context_snapshot_ids=(),
            successful_selection_snapshot_ids=(),
            timeline_evaluation_count=0,
            successful_selection_count=0,
            context_mismatch_count=0,
            baseline_integrity_failed_count=0,
            replay_incompatible_count=0,
            first_success_captured_at=None,
            last_success_captured_at=None,
            observation_span_hours=None,
            gross=(),
            turnover=None,
            cost_adjusted=(),
            status=status,
            safe_reason=reason,
            shadow_enrollment_verified=enrollment_verified,
            shadow_boundary_enforced=enrollment_verified,
            performance_as_of_enforced=True,
            evaluation_ceiling_enforced=status == NO_SHADOW_EVALUATIONS,
            stored_shadow_selection_reused=False,
            offline_replay_performed=False,
            outcome_data_used=False,
            performance_evaluated=False,
            cost_model_reused=False,
            turnover_continuity_enforced=False,
            database_write=False,
            external_calls=False,
            live_policy_change=False,
            sample_sufficiency_assessed=False,
            statistical_inference_performed=False,
            policy_decision_performed=False,
            promotion_performed=False,
            shadow_runtime_changed=False,
        )


__all__ = [
    "INSUFFICIENT_SHADOW_TRANSITIONS",
    "INVALID_SHADOW_OUTCOME_DATA",
    "INVALID_SHADOW_PERFORMANCE_EVIDENCE",
    "NO_SHADOW_COST_ADJUSTABLE_SNAPSHOTS",
    "NO_SHADOW_ENROLLMENT",
    "NO_SHADOW_EVALUATIONS",
    "NO_SUCCESSFUL_SHADOW_SELECTIONS",
    "RESULT_TYPE",
    "SHADOW_OUTCOMES_PENDING",
    "SUCCESS",
    "ShadowCostAdjustedHorizonEvidence",
    "ShadowCostAdjustedSnapshotEvidence",
    "ShadowGrossHorizonEvidence",
    "ShadowPolicyPerformanceResult",
    "ShadowPolicyPerformanceService",
    "ShadowTurnoverEvidence",
    "ShadowTurnoverTransitionEvidence",
]
