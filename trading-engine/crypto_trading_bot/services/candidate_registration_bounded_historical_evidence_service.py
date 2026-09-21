from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    INVALID_COST_ADJUSTED_DATA,
    CostAdjustedRankingEvaluationResult,
    CostAdjustedRankingEvaluationService,
    CostAssumptions,
    build_cost_assumptions,
)
from crypto_trading_bot.services.cost_adjusted_validation_robustness_service import (
    INVALID_COST_ADJUSTED_ROBUSTNESS_DATA,
    CostAdjustedValidationRobustnessResult,
    CostAdjustedValidationRobustnessService,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
    CostAdjustedWalkForwardValidationResult,
    CostAdjustedWalkForwardValidationService,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    InvalidForwardCandidateProvenance,
    ValidatedForwardCandidate,
    aware_utc,
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
    restore_weights,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    INVALID_SWEEP_DATA,
    RankingScenarioEvaluationMatrix,
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.ranking_validation_robustness_service import (
    INVALID_ROBUSTNESS_DATA,
    RankingValidationRobustnessResult,
    RankingValidationRobustnessService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import policy_signature
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    INVALID_TURNOVER_DATA,
    TemporalRankingTurnoverCohortResult,
    TemporalRankingTurnoverResult,
    TemporalRankingTurnoverService,
)


RESULT_TYPE = "REGISTRATION_TIME_BOUNDED_HISTORICAL_RANKING_EVIDENCE"
SUCCESS = "SUCCESS"
NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS = (
    "NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS"
)
INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE = (
    "INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE"
)


class _InvalidBoundedHistoricalEvidence(Exception):
    pass


@dataclass(frozen=True)
class CandidateRegistrationBoundedHistoricalEvidenceResult:
    candidate_id: int
    candidate: ForwardCandidateMetadata | None
    historical_evidence_as_of: datetime | None
    registration_snapshot_id_watermark: int | None
    registration_captured_at_watermark: datetime | None
    historical_timeline_snapshot_ids: tuple[int, ...]
    historical_candidate_snapshot_ids: tuple[int, ...]
    requested_horizons: tuple[int, ...]
    initial_research_size: int
    validation_size: int
    assumptions: CostAssumptions
    gross_matrix: RankingScenarioEvaluationMatrix | None
    gross_robustness: RankingValidationRobustnessResult | None
    turnover: TemporalRankingTurnoverResult | None
    target_turnover_cohort: TemporalRankingTurnoverCohortResult | None
    cost_adjusted: CostAdjustedRankingEvaluationResult | None
    cost_walk_forward: CostAdjustedWalkForwardValidationResult | None
    cost_robustness: CostAdjustedValidationRobustnessResult | None
    status: str
    safe_reason: str | None
    candidate_registration_verified: bool
    registration_time_evidence_enforced: bool
    post_registration_snapshots_excluded: bool
    post_registration_outcomes_excluded: bool
    snapshot_created_at_cutoff_enforced: bool
    outcome_evaluated_at_cutoff_enforced: bool
    outcome_created_at_cutoff_enforced: bool
    outcome_updated_at_cutoff_enforced: bool
    historical_forward_snapshot_disjointness_verified: bool
    historical_strict_unseen_validation: str
    sample_sufficiency_assessed: bool
    statistical_inference_performed: bool
    policy_decision_performed: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool


class CandidateRegistrationBoundedHistoricalEvidenceService:
    """Reconstruct research evidence that was knowable at candidate registration."""

    def __init__(
        self,
        session: Session,
        *,
        sweep_service: RankingScenarioSweepService | None = None,
        gross_robustness_service: RankingValidationRobustnessService | None = None,
        turnover_service: TemporalRankingTurnoverService | None = None,
        cost_service: CostAdjustedRankingEvaluationService | None = None,
        cost_walk_forward_service: CostAdjustedWalkForwardValidationService
        | None = None,
        cost_robustness_service: CostAdjustedValidationRobustnessService | None = None,
    ) -> None:
        self.session = session
        self.sweep_service = sweep_service or RankingScenarioSweepService(session)
        self.gross_robustness_service = (
            gross_robustness_service
            or RankingValidationRobustnessService(
                session, sweep_service=self.sweep_service
            )
        )
        self.turnover_service = turnover_service or TemporalRankingTurnoverService(
            session
        )
        self.cost_service = cost_service or CostAdjustedRankingEvaluationService(
            session,
            turnover_service=self.turnover_service,
            sweep_service=self.sweep_service,
        )
        self.cost_walk_forward_service = (
            cost_walk_forward_service
            or CostAdjustedWalkForwardValidationService(
                session, cost_adjusted_service=self.cost_service
            )
        )
        self.cost_robustness_service = (
            cost_robustness_service
            or CostAdjustedValidationRobustnessService(
                session,
                cost_adjusted_service=self.cost_service,
                walk_forward_service=self.cost_walk_forward_service,
            )
        )

    def evaluate(
        self,
        *,
        candidate_id: int,
        horizons: Iterable[int],
        initial_research_size: int,
        validation_size: int,
        fee_rate: object,
        spread_cost_rate: object,
        slippage_rate: object,
    ) -> CandidateRegistrationBoundedHistoricalEvidenceResult:
        normalized_horizons = RankingScenarioSweepService._validate_horizons(horizons)
        initial = self._positive_size(initial_research_size, "initial research size")
        validation = self._positive_size(validation_size, "validation size")
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        assumptions = build_cost_assumptions(
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )
        try:
            candidate = load_and_validate_forward_candidate(self.session, candidate_id)
            timeline, context = self._load_and_validate_snapshots(candidate)
            if not context:
                return self._result(
                    candidate,
                    normalized_horizons,
                    initial,
                    validation,
                    assumptions,
                    timeline,
                    context,
                    status=NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS,
                    safe_reason="no candidate-context snapshot existed at registration",
                )
            context_ids = tuple(snapshot.id for snapshot in context)
            timeline_ids = tuple(snapshot.id for snapshot in timeline)
            matrix = self.sweep_service.evaluate_matrix_snapshots(
                scenarios=(candidate.scenario,),
                horizons=normalized_horizons,
                snapshot_ids=context_ids,
                outcome_as_of=candidate.metadata.registered_at,
            )
            self._validate_matrix(candidate, context_ids, matrix)
            gross_robustness = self.gross_robustness_service.evaluate_from_matrix(
                matrix,
                initial_research_size=initial,
                validation_size=validation,
            )
            if any(
                cohort.status == INVALID_ROBUSTNESS_DATA
                for cohort in gross_robustness.cohorts
            ):
                raise _InvalidBoundedHistoricalEvidence(
                    "bounded gross robustness data is invalid"
                )
            turnover = self.turnover_service.evaluate_snapshots(
                scenarios=(candidate.scenario,), snapshot_ids=timeline_ids
            )
            target = self._target_turnover_cohort(candidate, turnover)
            cost = self.cost_service.evaluate_from_results(
                turnover,
                matrix,
                fee_rate=assumptions.fee_rate,
                spread_cost_rate=assumptions.spread_cost_rate,
                slippage_rate=assumptions.slippage_rate,
            )
            if cost.status == INVALID_COST_ADJUSTED_DATA:
                raise _InvalidBoundedHistoricalEvidence(
                    f"bounded cost-adjusted data is invalid: {cost.safe_reason}"
                )
            cost_walk = self.cost_walk_forward_service.evaluate_from_result(
                cost,
                initial_research_size=initial,
                validation_size=validation,
            )
            if cost_walk.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA:
                raise _InvalidBoundedHistoricalEvidence(
                    f"bounded cost walk-forward data is invalid: {cost_walk.safe_reason}"
                )
            cost_robustness = self.cost_robustness_service.evaluate_from_results(
                cost, cost_walk
            )
            if cost_robustness.status == INVALID_COST_ADJUSTED_ROBUSTNESS_DATA:
                raise _InvalidBoundedHistoricalEvidence(
                    "bounded cost robustness data is invalid: "
                    f"{cost_robustness.safe_reason}"
                )
            return self._result(
                candidate,
                normalized_horizons,
                initial,
                validation,
                assumptions,
                timeline,
                context,
                matrix=matrix,
                gross_robustness=gross_robustness,
                turnover=turnover,
                target_turnover_cohort=target,
                cost_adjusted=cost,
                cost_walk_forward=cost_walk,
                cost_robustness=cost_robustness,
                status=SUCCESS,
            )
        except (
            InvalidForwardCandidateProvenance,
            ReplayInputError,
            _InvalidBoundedHistoricalEvidence,
        ) as error:
            return self._invalid(
                candidate_id,
                normalized_horizons,
                initial,
                validation,
                assumptions,
                str(error),
            )

    def _load_and_validate_snapshots(
        self, candidate: ValidatedForwardCandidate
    ) -> tuple[tuple[StrategyReplaySnapshot, ...], tuple[StrategyReplaySnapshot, ...]]:
        metadata = candidate.metadata
        timeline = tuple(
            self.session.scalars(
                select(StrategyReplaySnapshot)
                .where(
                    StrategyReplaySnapshot.user_id == metadata.user_id,
                    StrategyReplaySnapshot.exchange == metadata.exchange,
                    StrategyReplaySnapshot.quote_asset == metadata.quote_asset,
                    StrategyReplaySnapshot.dataset_schema_version
                    == metadata.dataset_schema_version,
                    StrategyReplaySnapshot.id
                    <= metadata.registration_snapshot_id_watermark,
                    StrategyReplaySnapshot.captured_at
                    <= metadata.registration_captured_at_watermark,
                    StrategyReplaySnapshot.captured_at <= metadata.registered_at,
                    StrategyReplaySnapshot.created_at <= metadata.registered_at,
                )
                .order_by(
                    StrategyReplaySnapshot.captured_at.asc(),
                    StrategyReplaySnapshot.id.asc(),
                )
                .execution_options(autoflush=False)
            )
        )
        seen: set[int] = set()
        previous: tuple[datetime, int] | None = None
        context = []
        for snapshot in timeline:
            captured_at = aware_utc(snapshot.captured_at, "snapshot captured_at")
            created_at = aware_utc(snapshot.created_at, "snapshot created_at")
            key = (captured_at, snapshot.id)
            if snapshot.id in seen or (previous is not None and key <= previous):
                raise _InvalidBoundedHistoricalEvidence(
                    "historical snapshot chronology is invalid"
                )
            seen.add(snapshot.id)
            previous = key
            if (
                snapshot.id > metadata.registration_snapshot_id_watermark
                or captured_at > metadata.registration_captured_at_watermark
                or captured_at > metadata.registered_at
                or created_at > metadata.registered_at
            ):
                raise _InvalidBoundedHistoricalEvidence(
                    "post-registration snapshot leaked into historical timeline"
                )
            if (
                snapshot.policy_signature == metadata.baseline_policy_signature
                and self._stored_top_n(snapshot) == metadata.effective_top_n
            ):
                context.append(snapshot)
        return timeline, tuple(context)

    @staticmethod
    def _stored_top_n(snapshot: StrategyReplaySnapshot) -> int:
        if not isinstance(snapshot.policy_data, dict):
            raise _InvalidBoundedHistoricalEvidence("snapshot policy_data is invalid")
        if policy_signature(snapshot.policy_data) != snapshot.policy_signature:
            raise _InvalidBoundedHistoricalEvidence(
                "snapshot policy signature does not match policy_data"
            )
        try:
            _, top_n = restore_weights(snapshot.policy_data)
        except Exception as error:
            raise _InvalidBoundedHistoricalEvidence(
                f"snapshot ranking policy is invalid: {error}"
            ) from error
        return top_n

    @staticmethod
    def _validate_matrix(candidate, context_ids, matrix) -> None:
        if (
            matrix.requested_snapshot_count != len(context_ids)
            or matrix.evaluated_snapshot_count != len(context_ids)
            or matrix.scenarios != (candidate.scenario,)
            or any(
                cohort.status == INVALID_SWEEP_DATA
                or cohort.baseline_policy_signature
                != candidate.metadata.baseline_policy_signature
                or cohort.effective_top_n != candidate.metadata.effective_top_n
                or tuple(
                    value
                    for value in cohort.candidate_snapshot_ids
                    if value is not None
                )
                != context_ids
                for cohort in matrix.cohorts
            )
        ):
            raise _InvalidBoundedHistoricalEvidence(
                "bounded gross matrix snapshot lineage is invalid"
            )

    @staticmethod
    def _target_turnover_cohort(candidate, turnover):
        if turnover.status == INVALID_TURNOVER_DATA:
            raise _InvalidBoundedHistoricalEvidence(
                f"historical turnover is invalid: {turnover.safe_reason}"
            )
        matches = tuple(
            cohort
            for cohort in turnover.cohorts
            if cohort.baseline_policy_signature
            == candidate.metadata.baseline_policy_signature
            and cohort.effective_top_n == candidate.metadata.effective_top_n
        )
        if len(matches) > 1:
            raise _InvalidBoundedHistoricalEvidence(
                "candidate historical turnover cohort is duplicated"
            )
        return matches[0] if matches else None

    @staticmethod
    def _positive_size(value: int, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReplayInputError(f"{field_name} must be a positive integer")
        return value

    @staticmethod
    def _result(
        candidate,
        horizons,
        initial,
        validation,
        assumptions,
        timeline,
        context,
        *,
        matrix=None,
        gross_robustness=None,
        turnover=None,
        target_turnover_cohort=None,
        cost_adjusted=None,
        cost_walk_forward=None,
        cost_robustness=None,
        status,
        safe_reason=None,
    ):
        metadata = candidate.metadata
        return CandidateRegistrationBoundedHistoricalEvidenceResult(
            candidate_id=metadata.candidate_id,
            candidate=metadata,
            historical_evidence_as_of=metadata.registered_at,
            registration_snapshot_id_watermark=metadata.registration_snapshot_id_watermark,
            registration_captured_at_watermark=metadata.registration_captured_at_watermark,
            historical_timeline_snapshot_ids=tuple(item.id for item in timeline),
            historical_candidate_snapshot_ids=tuple(item.id for item in context),
            requested_horizons=horizons,
            initial_research_size=initial,
            validation_size=validation,
            assumptions=assumptions,
            gross_matrix=matrix,
            gross_robustness=gross_robustness,
            turnover=turnover,
            target_turnover_cohort=target_turnover_cohort,
            cost_adjusted=cost_adjusted,
            cost_walk_forward=cost_walk_forward,
            cost_robustness=cost_robustness,
            status=status,
            safe_reason=safe_reason,
            candidate_registration_verified=True,
            registration_time_evidence_enforced=True,
            post_registration_snapshots_excluded=True,
            post_registration_outcomes_excluded=True,
            snapshot_created_at_cutoff_enforced=True,
            outcome_evaluated_at_cutoff_enforced=True,
            outcome_created_at_cutoff_enforced=True,
            outcome_updated_at_cutoff_enforced=True,
            historical_forward_snapshot_disjointness_verified=True,
            historical_strict_unseen_validation="not_verified",
            sample_sufficiency_assessed=False,
            statistical_inference_performed=False,
            policy_decision_performed=False,
            database_write=False,
            external_calls=False,
            live_policy_change=False,
        )

    @staticmethod
    def _invalid(candidate_id, horizons, initial, validation, assumptions, reason):
        return CandidateRegistrationBoundedHistoricalEvidenceResult(
            candidate_id=candidate_id,
            candidate=None,
            historical_evidence_as_of=None,
            registration_snapshot_id_watermark=None,
            registration_captured_at_watermark=None,
            historical_timeline_snapshot_ids=(),
            historical_candidate_snapshot_ids=(),
            requested_horizons=horizons,
            initial_research_size=initial,
            validation_size=validation,
            assumptions=assumptions,
            gross_matrix=None,
            gross_robustness=None,
            turnover=None,
            target_turnover_cohort=None,
            cost_adjusted=None,
            cost_walk_forward=None,
            cost_robustness=None,
            status=INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE,
            safe_reason=reason,
            candidate_registration_verified=False,
            registration_time_evidence_enforced=False,
            post_registration_snapshots_excluded=False,
            post_registration_outcomes_excluded=False,
            snapshot_created_at_cutoff_enforced=False,
            outcome_evaluated_at_cutoff_enforced=False,
            outcome_created_at_cutoff_enforced=False,
            outcome_updated_at_cutoff_enforced=False,
            historical_forward_snapshot_disjointness_verified=False,
            historical_strict_unseen_validation="not_verified",
            sample_sufficiency_assessed=False,
            statistical_inference_performed=False,
            policy_decision_performed=False,
            database_write=False,
            external_calls=False,
            live_policy_change=False,
        )


__all__ = [
    "INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE",
    "NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS",
    "RESULT_TYPE",
    "SUCCESS",
    "CandidateRegistrationBoundedHistoricalEvidenceResult",
    "CandidateRegistrationBoundedHistoricalEvidenceService",
]
