from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    CostAssumptions,
    InvalidCostAdjustedData,
    build_cost_assumptions,
    compute_selection_change_cost,
)
from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    FORWARD_OUTCOMES_PENDING as GROSS_OUTCOMES_PENDING,
    INVALID_FORWARD_EVIDENCE,
    NO_COMPARABLE_FORWARD_SNAPSHOTS,
    NO_FORWARD_SNAPSHOTS as GROSS_NO_FORWARD_SNAPSHOTS,
    SUCCESS as GROSS_SUCCESS,
    ForwardCandidateGrossEvidenceResult,
    ForwardCandidateGrossEvidenceService,
    ForwardCandidateHorizonEvidence,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
)
from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
    INSUFFICIENT_FORWARD_TRANSITIONS as TURNOVER_INSUFFICIENT,
    INVALID_FORWARD_TURNOVER,
    NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS,
    NO_FORWARD_SNAPSHOTS as TURNOVER_NO_FORWARD_SNAPSHOTS,
    SUCCESS as TURNOVER_SUCCESS,
    ForwardCandidateTurnoverEvidenceResult,
    ForwardCandidateTurnoverEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    INVALID_OUTCOME_DATA,
    OUTCOME_INCOMPLETE,
    REPLAY_INCOMPATIBLE,
    SUCCESS as AB_SUCCESS,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    RankingSelectionTransition,
    TemporalRankingTurnoverScenarioTransition,
    TemporalRankingTurnoverTransition,
)


RESULT_TYPE = "FORWARD_ONLY_COST_ADJUSTED_RANKING_SELECTION_EVIDENCE"
SUCCESS = "SUCCESS"
NO_FORWARD_SNAPSHOTS = "NO_FORWARD_SNAPSHOTS"
INSUFFICIENT_FORWARD_TRANSITIONS = "INSUFFICIENT_FORWARD_TRANSITIONS"
FORWARD_OUTCOMES_PENDING = "FORWARD_OUTCOMES_PENDING"
NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS = "NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS"
INVALID_FORWARD_COST_ADJUSTED_EVIDENCE = "INVALID_FORWARD_COST_ADJUSTED_EVIDENCE"
_KNOWN_GROSS_STATUSES = frozenset(
    {
        GROSS_SUCCESS,
        GROSS_NO_FORWARD_SNAPSHOTS,
        GROSS_OUTCOMES_PENDING,
        NO_COMPARABLE_FORWARD_SNAPSHOTS,
        INVALID_FORWARD_EVIDENCE,
    }
)
_KNOWN_TURNOVER_STATUSES = frozenset(
    {
        TURNOVER_SUCCESS,
        TURNOVER_NO_FORWARD_SNAPSHOTS,
        TURNOVER_INSUFFICIENT,
        NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS,
        INVALID_FORWARD_TURNOVER,
    }
)
_KNOWN_SNAPSHOT_STATUSES = frozenset(
    {
        AB_SUCCESS,
        OUTCOME_INCOMPLETE,
        BASELINE_INTEGRITY_FAILED,
        REPLAY_INCOMPATIBLE,
        INVALID_OUTCOME_DATA,
    }
)


class _InvalidForwardCostAdjustedEvidence(Exception):
    pass


@dataclass(frozen=True)
class ForwardCandidateCostAdjustedSnapshotEvidence:
    snapshot_id: int
    pipeline_run_id: str
    captured_at: datetime
    horizon_minutes: int
    previous_snapshot_id: int
    previous_captured_at: datetime
    candidate_id: int
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
    candidate_replacement_rate: Decimal
    baseline_sell_notional_ratio: Decimal
    baseline_buy_notional_ratio: Decimal
    baseline_gross_traded_notional_ratio: Decimal
    baseline_execution_cost_ratio: Decimal
    baseline_execution_cost_percentage: Decimal
    candidate_sell_notional_ratio: Decimal
    candidate_buy_notional_ratio: Decimal
    candidate_gross_traded_notional_ratio: Decimal
    candidate_execution_cost_ratio: Decimal
    candidate_execution_cost_percentage: Decimal
    baseline_gross_return: Decimal
    candidate_gross_return: Decimal
    gross_return_delta: Decimal
    baseline_cost_adjusted_return: Decimal
    candidate_cost_adjusted_return: Decimal
    cost_adjusted_return_delta: Decimal
    cost_adjusted_scenario_result: str


@dataclass(frozen=True)
class ForwardCandidateCostAdjustedHorizonEvidence:
    horizon_minutes: int
    eligible_forward_snapshot_count: int
    successful_gross_snapshot_count: int
    forward_transition_count: int
    cost_adjustable_forward_snapshot_count: int
    cost_adjustable_forward_snapshot_ids: tuple[int, ...]
    cost_adjustable_coverage_rate: Decimal | None
    outcome_incomplete_count: int
    mean_baseline_gross_return: Decimal | None
    mean_candidate_gross_return: Decimal | None
    mean_gross_return_delta: Decimal | None
    mean_baseline_replacement_rate: Decimal | None
    mean_candidate_replacement_rate: Decimal | None
    mean_baseline_execution_cost_percentage: Decimal | None
    mean_candidate_execution_cost_percentage: Decimal | None
    mean_execution_cost_delta_percentage: Decimal | None
    mean_baseline_cost_adjusted_return: Decimal | None
    mean_candidate_cost_adjusted_return: Decimal | None
    mean_cost_adjusted_return_delta: Decimal | None
    median_cost_adjusted_return_delta: Decimal | None
    cost_adjusted_win_count: int
    cost_adjusted_loss_count: int
    tie_count: int
    cost_adjusted_win_rate: Decimal | None
    status: str
    safe_reason: str | None
    snapshots: tuple[ForwardCandidateCostAdjustedSnapshotEvidence, ...]


@dataclass(frozen=True)
class ForwardCandidateCostAdjustedEvidenceResult:
    candidate_id: int
    candidate: ForwardCandidateMetadata | None
    requested_horizons: tuple[int, ...]
    assumptions: CostAssumptions
    gross_status: str
    turnover_status: str
    gross_forward_snapshot_ids: tuple[int, ...]
    turnover_current_snapshot_ids: tuple[int, ...]
    turnover_transition_count: int
    continuity_break_count: int
    status: str
    safe_reason: str | None
    candidate_registration_verified: bool
    forward_anchor_enforced: bool
    pre_registration_snapshots_excluded: bool
    pre_registration_transition_excluded: bool
    forward_continuity_enforced: bool
    cost_model_reused: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    sample_sufficiency_assessed: bool
    statistical_inference_performed: bool
    policy_decision_performed: bool
    horizons: tuple[ForwardCandidateCostAdjustedHorizonEvidence, ...]


class ForwardCandidateCostAdjustedEvidenceService:
    """Apply the historical cost proxy to aligned immutable Forward evidence."""

    def __init__(
        self,
        session: Session,
        *,
        gross_service: ForwardCandidateGrossEvidenceService | None = None,
        turnover_service: ForwardCandidateTurnoverEvidenceService | None = None,
    ) -> None:
        self.gross_service = gross_service or ForwardCandidateGrossEvidenceService(
            session
        )
        self.turnover_service = (
            turnover_service or ForwardCandidateTurnoverEvidenceService(session)
        )

    def evaluate(
        self,
        *,
        candidate_id: int,
        horizons: Iterable[int],
        fee_rate: object,
        spread_cost_rate: object,
        slippage_rate: object,
    ) -> ForwardCandidateCostAdjustedEvidenceResult:
        normalized_horizons = RankingScenarioSweepService._validate_horizons(horizons)
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        gross = self.gross_service.evaluate(
            candidate_id=candidate_id, horizons=normalized_horizons
        )
        turnover = self.turnover_service.evaluate(candidate_id=candidate_id)
        return self.evaluate_from_results(
            gross,
            turnover,
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )

    def evaluate_from_results(
        self,
        gross: ForwardCandidateGrossEvidenceResult,
        turnover: ForwardCandidateTurnoverEvidenceResult,
        *,
        fee_rate: object,
        spread_cost_rate: object,
        slippage_rate: object,
    ) -> ForwardCandidateCostAdjustedEvidenceResult:
        assumptions = build_cost_assumptions(
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )
        try:
            candidate = self._validate_upstreams(gross, turnover)
            transitions = self._index_transitions(turnover, candidate)
            horizon_results = tuple(
                self._evaluate_horizon(
                    gross_horizon,
                    candidate,
                    gross.eligible_forward_snapshot_ids,
                    transitions,
                    assumptions,
                )
                for gross_horizon in gross.horizons
            )
            status, reason = self._overall_status(gross, turnover, horizon_results)
            return ForwardCandidateCostAdjustedEvidenceResult(
                candidate_id=gross.candidate_id,
                candidate=candidate,
                requested_horizons=gross.requested_horizons,
                assumptions=assumptions,
                gross_status=gross.status,
                turnover_status=turnover.status,
                gross_forward_snapshot_ids=gross.eligible_forward_snapshot_ids,
                turnover_current_snapshot_ids=tuple(transitions),
                turnover_transition_count=len(transitions),
                continuity_break_count=turnover.continuity_break_count,
                status=status,
                safe_reason=reason,
                candidate_registration_verified=True,
                forward_anchor_enforced=True,
                pre_registration_snapshots_excluded=True,
                pre_registration_transition_excluded=True,
                forward_continuity_enforced=True,
                cost_model_reused=True,
                database_write=False,
                external_calls=False,
                live_policy_change=False,
                sample_sufficiency_assessed=False,
                statistical_inference_performed=False,
                policy_decision_performed=False,
                horizons=horizon_results,
            )
        except (_InvalidForwardCostAdjustedEvidence, InvalidCostAdjustedData) as error:
            return self._invalid(gross, turnover, assumptions, str(error))

    @staticmethod
    def _validate_upstreams(
        gross: ForwardCandidateGrossEvidenceResult,
        turnover: ForwardCandidateTurnoverEvidenceResult,
    ) -> ForwardCandidateMetadata:
        if gross.status not in _KNOWN_GROSS_STATUSES:
            raise _InvalidForwardCostAdjustedEvidence("gross status is unsupported")
        if turnover.status not in _KNOWN_TURNOVER_STATUSES:
            raise _InvalidForwardCostAdjustedEvidence("turnover status is unsupported")
        if (
            gross.status == INVALID_FORWARD_EVIDENCE
            or turnover.status == INVALID_FORWARD_TURNOVER
        ):
            raise _InvalidForwardCostAdjustedEvidence(
                "upstream Forward evidence is invalid"
            )
        if gross.candidate is None or turnover.candidate is None:
            raise _InvalidForwardCostAdjustedEvidence(
                "upstream candidate metadata is missing"
            )
        if (
            gross.candidate_id != turnover.candidate_id
            or gross.candidate_id != gross.candidate.candidate_id
            or turnover.candidate_id != turnover.candidate.candidate_id
            or gross.candidate != turnover.candidate
        ):
            raise _InvalidForwardCostAdjustedEvidence(
                "Gross and Turnover candidate metadata do not match"
            )
        gross_flags = (
            gross.candidate_registration_verified,
            gross.forward_anchor_enforced,
            gross.pre_registration_snapshots_excluded,
            gross.registration_time_provenance_verified,
            gross.future_snapshot_cutoff_verified,
            gross.scenario_definition_frozen_at_registration,
            gross.forward_validation_performed,
        )
        turnover_flags = (
            turnover.candidate_registration_verified,
            turnover.forward_anchor_enforced,
            turnover.pre_registration_snapshots_excluded,
            turnover.pre_registration_transition_excluded,
            turnover.forward_continuity_enforced,
            turnover.first_forward_snapshot_has_no_prior_forward_transition,
        )
        if not all(gross_flags) or not all(turnover_flags):
            raise _InvalidForwardCostAdjustedEvidence(
                "upstream Forward provenance flags are incomplete"
            )
        gross_ids = gross.eligible_forward_snapshot_ids
        candidate_ids = turnover.candidate_context_snapshot_ids
        timeline_ids = turnover.forward_timeline_snapshot_ids
        timeline_positions = {
            snapshot_id: index for index, snapshot_id in enumerate(timeline_ids)
        }
        if (
            len(gross_ids) != len(set(gross_ids))
            or len(candidate_ids) != len(set(candidate_ids))
            or len(timeline_ids) != len(timeline_positions)
            or gross_ids != candidate_ids
            or any(
                snapshot_id not in timeline_positions for snapshot_id in candidate_ids
            )
            or tuple(sorted(candidate_ids, key=timeline_positions.__getitem__))
            != candidate_ids
        ):
            raise _InvalidForwardCostAdjustedEvidence(
                "Gross and Turnover candidate snapshot sets do not match"
            )
        if gross.requested_horizons != tuple(
            sorted(set(gross.requested_horizons))
        ) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in gross.requested_horizons
        ):
            raise _InvalidForwardCostAdjustedEvidence(
                "requested horizon metadata is invalid"
            )
        horizons = tuple(item.horizon_minutes for item in gross.horizons)
        if horizons != gross.requested_horizons or len(horizons) != len(set(horizons)):
            raise _InvalidForwardCostAdjustedEvidence(
                "gross horizon result set does not match the request"
            )
        expected_gross_status = (
            GROSS_NO_FORWARD_SNAPSHOTS
            if not gross_ids
            else GROSS_SUCCESS
            if any(item.status == GROSS_SUCCESS for item in gross.horizons)
            else GROSS_OUTCOMES_PENDING
            if all(item.status == GROSS_OUTCOMES_PENDING for item in gross.horizons)
            else NO_COMPARABLE_FORWARD_SNAPSHOTS
        )
        if gross.status != expected_gross_status:
            raise _InvalidForwardCostAdjustedEvidence(
                "gross aggregate status is inconsistent"
            )
        if (not gross_ids) != (gross.status == GROSS_NO_FORWARD_SNAPSHOTS) or (
            not turnover.forward_timeline_snapshot_ids
        ) != (turnover.status == TURNOVER_NO_FORWARD_SNAPSHOTS):
            raise _InvalidForwardCostAdjustedEvidence(
                "upstream no-forward status is inconsistent"
            )
        expected_turnover_status = (
            TURNOVER_NO_FORWARD_SNAPSHOTS
            if not turnover.forward_timeline_snapshot_ids
            else TURNOVER_SUCCESS
            if turnover.transitions
            else TURNOVER_INSUFFICIENT
            if turnover.candidate_context_snapshot_ids
            else NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS
        )
        if turnover.status != expected_turnover_status:
            raise _InvalidForwardCostAdjustedEvidence(
                "turnover aggregate status is inconsistent"
            )
        return gross.candidate

    @classmethod
    def _index_transitions(
        cls,
        turnover: ForwardCandidateTurnoverEvidenceResult,
        candidate: ForwardCandidateMetadata,
    ) -> dict[int, TemporalRankingTurnoverTransition]:
        if turnover.transition_count != len(turnover.transitions):
            raise _InvalidForwardCostAdjustedEvidence(
                "turnover transition count mismatch"
            )
        timeline_positions = {
            snapshot_id: index
            for index, snapshot_id in enumerate(turnover.forward_timeline_snapshot_ids)
        }
        candidate_ids = set(turnover.candidate_context_snapshot_ids)
        indexed: dict[int, TemporalRankingTurnoverTransition] = {}
        for expected_index, transition in enumerate(turnover.transitions, start=1):
            baseline = transition.baseline
            current_id = baseline.current_snapshot_id
            previous_id = baseline.previous_snapshot_id
            if current_id in indexed:
                raise _InvalidForwardCostAdjustedEvidence(
                    "duplicate turnover current snapshot"
                )
            scenario = cls._candidate_transition(transition, candidate.scenario_name)
            if (
                transition.transition_index != expected_index
                or previous_id not in candidate_ids
                or current_id not in candidate_ids
                or previous_id not in timeline_positions
                or current_id not in timeline_positions
                or timeline_positions[current_id] != timeline_positions[previous_id] + 1
                or baseline.effective_top_n != candidate.effective_top_n
                or scenario.scenario_name != candidate.scenario_name
                or scenario.transition.previous_snapshot_id != previous_id
                or scenario.transition.current_snapshot_id != current_id
                or scenario.transition.effective_top_n != candidate.effective_top_n
            ):
                raise _InvalidForwardCostAdjustedEvidence(
                    "turnover transition lineage is invalid"
                )
            indexed[current_id] = transition
        if bool(indexed) != (turnover.status == TURNOVER_SUCCESS):
            raise _InvalidForwardCostAdjustedEvidence(
                "turnover status and transition set are inconsistent"
            )
        return indexed

    def _evaluate_horizon(
        self,
        horizon: ForwardCandidateHorizonEvidence,
        candidate: ForwardCandidateMetadata,
        eligible_ids: tuple[int, ...],
        transitions: dict[int, TemporalRankingTurnoverTransition],
        assumptions: CostAssumptions,
    ) -> ForwardCandidateCostAdjustedHorizonEvidence:
        rows = self._index_gross_snapshots(horizon, eligible_ids, candidate)
        successful_ids = tuple(
            snapshot_id
            for snapshot_id, row in rows.items()
            if row.status == AB_SUCCESS and row.performance_evaluated is True
        )
        adjustable_ids = tuple(
            snapshot_id for snapshot_id in transitions if snapshot_id in successful_ids
        )
        snapshots = tuple(
            self._snapshot_result(
                rows[snapshot_id],
                transitions[snapshot_id],
                candidate,
                assumptions,
            )
            for snapshot_id in adjustable_ids
        )
        status, reason = self._horizon_status(
            rows, transitions, snapshots, eligible_ids
        )
        baseline_costs = tuple(
            item.baseline_execution_cost_percentage for item in snapshots
        )
        candidate_costs = tuple(
            item.candidate_execution_cost_percentage for item in snapshots
        )
        adjusted_deltas = tuple(item.cost_adjusted_return_delta for item in snapshots)
        wins = sum(value > 0 for value in adjusted_deltas)
        losses = sum(value < 0 for value in adjusted_deltas)
        ties = sum(value == 0 for value in adjusted_deltas)
        successful_count = len(successful_ids)
        return ForwardCandidateCostAdjustedHorizonEvidence(
            horizon_minutes=horizon.horizon_minutes,
            eligible_forward_snapshot_count=len(eligible_ids),
            successful_gross_snapshot_count=successful_count,
            forward_transition_count=len(transitions),
            cost_adjustable_forward_snapshot_count=len(snapshots),
            cost_adjustable_forward_snapshot_ids=adjustable_ids,
            cost_adjustable_coverage_rate=(
                Decimal(len(snapshots)) / Decimal(successful_count)
                if successful_count
                else None
            ),
            outcome_incomplete_count=horizon.outcome_incomplete_count,
            mean_baseline_gross_return=self._mean(
                tuple(item.baseline_gross_return for item in snapshots)
            ),
            mean_candidate_gross_return=self._mean(
                tuple(item.candidate_gross_return for item in snapshots)
            ),
            mean_gross_return_delta=self._mean(
                tuple(item.gross_return_delta for item in snapshots)
            ),
            mean_baseline_replacement_rate=self._mean(
                tuple(item.baseline_replacement_rate for item in snapshots)
            ),
            mean_candidate_replacement_rate=self._mean(
                tuple(item.candidate_replacement_rate for item in snapshots)
            ),
            mean_baseline_execution_cost_percentage=self._mean(baseline_costs),
            mean_candidate_execution_cost_percentage=self._mean(candidate_costs),
            mean_execution_cost_delta_percentage=self._mean(
                tuple(
                    candidate_cost - baseline_cost
                    for candidate_cost, baseline_cost in zip(
                        candidate_costs, baseline_costs, strict=True
                    )
                )
            ),
            mean_baseline_cost_adjusted_return=self._mean(
                tuple(item.baseline_cost_adjusted_return for item in snapshots)
            ),
            mean_candidate_cost_adjusted_return=self._mean(
                tuple(item.candidate_cost_adjusted_return for item in snapshots)
            ),
            mean_cost_adjusted_return_delta=self._mean(adjusted_deltas),
            median_cost_adjusted_return_delta=(
                median(adjusted_deltas) if adjusted_deltas else None
            ),
            cost_adjusted_win_count=wins,
            cost_adjusted_loss_count=losses,
            tie_count=ties,
            cost_adjusted_win_rate=(
                Decimal(wins) / Decimal(len(adjusted_deltas))
                if adjusted_deltas
                else None
            ),
            status=status,
            safe_reason=reason,
            snapshots=snapshots,
        )

    @staticmethod
    def _index_gross_snapshots(
        horizon: ForwardCandidateHorizonEvidence,
        eligible_ids: tuple[int, ...],
        candidate: ForwardCandidateMetadata,
    ) -> dict[int, StrategyABSnapshotPerformanceResult]:
        if horizon.eligible_forward_snapshot_count != len(eligible_ids):
            raise _InvalidForwardCostAdjustedEvidence(
                "gross eligible snapshot count mismatch"
            )
        indexed: dict[int, StrategyABSnapshotPerformanceResult] = {}
        for row in horizon.snapshots:
            if (
                isinstance(row.snapshot_id, bool)
                or not isinstance(row.snapshot_id, int)
                or row.snapshot_id < 1
                or row.snapshot_id in indexed
            ):
                raise _InvalidForwardCostAdjustedEvidence(
                    "duplicate or invalid Gross snapshot ID"
                )
            if (
                row.snapshot_id not in eligible_ids
                or row.horizon_minutes != horizon.horizon_minutes
                or row.baseline_policy_signature != candidate.baseline_policy_signature
                or row.status not in _KNOWN_SNAPSHOT_STATUSES
                or (row.status == AB_SUCCESS) is not row.performance_evaluated
            ):
                raise _InvalidForwardCostAdjustedEvidence(
                    "Gross snapshot lineage is invalid"
                )
            indexed[row.snapshot_id] = row
        if tuple(indexed) != eligible_ids:
            raise _InvalidForwardCostAdjustedEvidence(
                "Gross horizon snapshot set does not match eligible snapshots"
            )
        success_count = sum(row.status == AB_SUCCESS for row in indexed.values())
        pending_count = sum(
            row.status == OUTCOME_INCOMPLETE for row in indexed.values()
        )
        baseline_failed_count = sum(
            row.status == BASELINE_INTEGRITY_FAILED for row in indexed.values()
        )
        replay_failed_count = sum(
            row.status == REPLAY_INCOMPATIBLE for row in indexed.values()
        )
        invalid_outcome_count = sum(
            row.status == INVALID_OUTCOME_DATA for row in indexed.values()
        )
        if (
            success_count != horizon.successful_comparable_snapshot_count
            or pending_count != horizon.outcome_incomplete_count
            or baseline_failed_count != horizon.baseline_integrity_failed_count
            or replay_failed_count != horizon.replay_incompatible_count
            or invalid_outcome_count != horizon.invalid_outcome_count
            or (
                success_count
                + pending_count
                + baseline_failed_count
                + replay_failed_count
                + invalid_outcome_count
                != len(indexed)
            )
        ):
            raise _InvalidForwardCostAdjustedEvidence(
                "Gross horizon status counts are inconsistent"
            )
        expected_status = (
            GROSS_NO_FORWARD_SNAPSHOTS
            if not eligible_ids
            else GROSS_SUCCESS
            if success_count
            else GROSS_OUTCOMES_PENDING
            if pending_count == len(indexed)
            else NO_COMPARABLE_FORWARD_SNAPSHOTS
        )
        if horizon.status != expected_status:
            raise _InvalidForwardCostAdjustedEvidence(
                "Gross horizon aggregate status is inconsistent"
            )
        return indexed

    @classmethod
    def _snapshot_result(
        cls,
        gross: StrategyABSnapshotPerformanceResult,
        transition: TemporalRankingTurnoverTransition,
        candidate: ForwardCandidateMetadata,
        assumptions: CostAssumptions,
    ) -> ForwardCandidateCostAdjustedSnapshotEvidence:
        baseline = transition.baseline
        scenario = cls._candidate_transition(transition, candidate.scenario_name)
        cls._validate_cross_result_lineage(gross, baseline, scenario, candidate)
        baseline_cost = compute_selection_change_cost(baseline, assumptions)
        candidate_cost = compute_selection_change_cost(scenario.transition, assumptions)
        baseline_return = cls._decimal(
            gross.baseline_mean_return, "baseline_mean_return"
        )
        candidate_return = cls._decimal(
            gross.scenario_mean_return, "scenario_mean_return"
        )
        gross_delta = cls._decimal(gross.mean_return_delta, "mean_return_delta")
        baseline_adjusted = baseline_return - baseline_cost.execution_cost_percentage
        candidate_adjusted = candidate_return - candidate_cost.execution_cost_percentage
        adjusted_delta = candidate_adjusted - baseline_adjusted
        return ForwardCandidateCostAdjustedSnapshotEvidence(
            snapshot_id=gross.snapshot_id,
            pipeline_run_id=gross.pipeline_run_id,
            captured_at=gross.captured_at.astimezone(UTC),
            horizon_minutes=gross.horizon_minutes,
            previous_snapshot_id=baseline.previous_snapshot_id,
            previous_captured_at=baseline.previous_captured_at.astimezone(UTC),
            candidate_id=candidate.candidate_id,
            baseline_policy_signature=candidate.baseline_policy_signature,
            effective_top_n=candidate.effective_top_n,
            scenario_name=candidate.scenario_name,
            scenario_definition_signature=candidate.scenario_definition_signature,
            scenario_signature=gross.scenario_signature,
            fee_rate=assumptions.fee_rate,
            spread_cost_rate=assumptions.spread_cost_rate,
            slippage_rate=assumptions.slippage_rate,
            total_cost_rate=assumptions.total_cost_rate,
            target_weight=baseline_cost.target_weight,
            baseline_replacement_rate=baseline_cost.replacement_rate,
            candidate_replacement_rate=candidate_cost.replacement_rate,
            baseline_sell_notional_ratio=baseline_cost.sell_notional_ratio,
            baseline_buy_notional_ratio=baseline_cost.buy_notional_ratio,
            baseline_gross_traded_notional_ratio=(
                baseline_cost.gross_traded_notional_ratio
            ),
            baseline_execution_cost_ratio=baseline_cost.execution_cost_ratio,
            baseline_execution_cost_percentage=(
                baseline_cost.execution_cost_percentage
            ),
            candidate_sell_notional_ratio=candidate_cost.sell_notional_ratio,
            candidate_buy_notional_ratio=candidate_cost.buy_notional_ratio,
            candidate_gross_traded_notional_ratio=(
                candidate_cost.gross_traded_notional_ratio
            ),
            candidate_execution_cost_ratio=candidate_cost.execution_cost_ratio,
            candidate_execution_cost_percentage=(
                candidate_cost.execution_cost_percentage
            ),
            baseline_gross_return=baseline_return,
            candidate_gross_return=candidate_return,
            gross_return_delta=gross_delta,
            baseline_cost_adjusted_return=baseline_adjusted,
            candidate_cost_adjusted_return=candidate_adjusted,
            cost_adjusted_return_delta=adjusted_delta,
            cost_adjusted_scenario_result=(
                "COST_ADJUSTED_SCENARIO_WIN"
                if adjusted_delta > 0
                else "COST_ADJUSTED_SCENARIO_LOSS"
                if adjusted_delta < 0
                else "COST_ADJUSTED_TIE"
            ),
        )

    @staticmethod
    def _validate_cross_result_lineage(
        gross: StrategyABSnapshotPerformanceResult,
        baseline: RankingSelectionTransition,
        scenario: TemporalRankingTurnoverScenarioTransition,
        candidate: ForwardCandidateMetadata,
    ) -> None:
        if (
            gross.status != AB_SUCCESS
            or gross.performance_evaluated is not True
            or not isinstance(gross.pipeline_run_id, str)
            or not gross.pipeline_run_id
            or not isinstance(gross.captured_at, datetime)
            or gross.captured_at.tzinfo is None
            or gross.captured_at.astimezone(UTC) != baseline.current_captured_at
            or gross.snapshot_id != baseline.current_snapshot_id
            or gross.baseline_policy_signature != candidate.baseline_policy_signature
            or gross.effective_top_n != candidate.effective_top_n
            or baseline.effective_top_n != candidate.effective_top_n
            or scenario.transition.effective_top_n != candidate.effective_top_n
            or gross.baseline_top_markets != baseline.current_top_markets
            or gross.scenario_top_markets != scenario.transition.current_top_markets
            or scenario.scenario_name != candidate.scenario_name
            or not isinstance(gross.scenario_signature, str)
            or not gross.scenario_signature
            or gross.scenario_signature != scenario.scenario_signature
            or scenario.transition.previous_snapshot_id != baseline.previous_snapshot_id
            or scenario.transition.current_snapshot_id != baseline.current_snapshot_id
            or scenario.transition.previous_captured_at != baseline.previous_captured_at
            or scenario.transition.current_captured_at != baseline.current_captured_at
        ):
            raise _InvalidForwardCostAdjustedEvidence(
                f"Gross/Turnover snapshot lineage mismatch: {gross.snapshot_id}"
            )

    @staticmethod
    def _candidate_transition(
        transition: TemporalRankingTurnoverTransition, scenario_name: str
    ) -> TemporalRankingTurnoverScenarioTransition:
        matches = tuple(
            item for item in transition.scenarios if item.scenario_name == scenario_name
        )
        if len(matches) != 1:
            raise _InvalidForwardCostAdjustedEvidence(
                "Candidate turnover transition is missing or duplicated"
            )
        return matches[0]

    @staticmethod
    def _horizon_status(rows, transitions, snapshots, eligible_ids):
        if not eligible_ids:
            return NO_FORWARD_SNAPSHOTS, "no genuine Forward snapshot"
        if not transitions:
            return (
                INSUFFICIENT_FORWARD_TRANSITIONS,
                "no Forward-to-Forward transition is available",
            )
        if snapshots:
            return SUCCESS, None
        target_rows = tuple(rows[snapshot_id] for snapshot_id in transitions)
        if target_rows and all(row.status == OUTCOME_INCOMPLETE for row in target_rows):
            return (
                FORWARD_OUTCOMES_PENDING,
                "all turnover target outcomes are incomplete",
            )
        return (
            NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS,
            "Gross SUCCESS and turnover current snapshots do not overlap",
        )

    @staticmethod
    def _overall_status(gross, turnover, horizons):
        if not gross.eligible_forward_snapshot_ids:
            return NO_FORWARD_SNAPSHOTS, "no genuine Forward snapshot"
        if not turnover.transitions:
            return (
                INSUFFICIENT_FORWARD_TRANSITIONS,
                "no Forward-to-Forward transition is available",
            )
        if any(item.status == SUCCESS for item in horizons):
            return SUCCESS, None
        if horizons and all(
            item.status == FORWARD_OUTCOMES_PENDING for item in horizons
        ):
            return (
                FORWARD_OUTCOMES_PENDING,
                "all requested horizons await Forward outcomes",
            )
        return (
            NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS,
            "no requested horizon has a cost-adjustable Forward snapshot",
        )

    @staticmethod
    def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
        return sum(values, Decimal("0")) / len(values) if values else None

    @staticmethod
    def _decimal(value: object, field_name: str) -> Decimal:
        if isinstance(value, bool):
            raise _InvalidForwardCostAdjustedEvidence(f"{field_name} is invalid")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise _InvalidForwardCostAdjustedEvidence(
                f"{field_name} is invalid"
            ) from error
        if not parsed.is_finite():
            raise _InvalidForwardCostAdjustedEvidence(f"{field_name} must be finite")
        return parsed

    @staticmethod
    def _invalid(gross, turnover, assumptions, reason):
        candidate = gross.candidate if gross.candidate == turnover.candidate else None
        return ForwardCandidateCostAdjustedEvidenceResult(
            candidate_id=gross.candidate_id,
            candidate=candidate,
            requested_horizons=gross.requested_horizons,
            assumptions=assumptions,
            gross_status=gross.status,
            turnover_status=turnover.status,
            gross_forward_snapshot_ids=gross.eligible_forward_snapshot_ids,
            turnover_current_snapshot_ids=(),
            turnover_transition_count=turnover.transition_count,
            continuity_break_count=turnover.continuity_break_count,
            status=INVALID_FORWARD_COST_ADJUSTED_EVIDENCE,
            safe_reason=reason,
            candidate_registration_verified=False,
            forward_anchor_enforced=False,
            pre_registration_snapshots_excluded=False,
            pre_registration_transition_excluded=False,
            forward_continuity_enforced=False,
            cost_model_reused=True,
            database_write=False,
            external_calls=False,
            live_policy_change=False,
            sample_sufficiency_assessed=False,
            statistical_inference_performed=False,
            policy_decision_performed=False,
            horizons=(),
        )


__all__ = [
    "COST_ADJUSTED_METRIC_TYPE",
    "FORWARD_OUTCOMES_PENDING",
    "GROSS_PERFORMANCE_METRIC_TYPE",
    "INSUFFICIENT_FORWARD_TRANSITIONS",
    "INVALID_FORWARD_COST_ADJUSTED_EVIDENCE",
    "NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS",
    "NO_FORWARD_SNAPSHOTS",
    "RESULT_TYPE",
    "SUCCESS",
    "ForwardCandidateCostAdjustedEvidenceResult",
    "ForwardCandidateCostAdjustedEvidenceService",
    "ForwardCandidateCostAdjustedHorizonEvidence",
    "ForwardCandidateCostAdjustedSnapshotEvidence",
]
