"""Reference-time-bounded orchestration for generated ranking research candidates."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    CostAdjustedRankingEvaluationResult,
    CostAdjustedRankingEvaluationService,
    CostAdjustedRankingScenarioResult,
    CostAssumptions,
    build_cost_assumptions,
)
from crypto_trading_bot.services.cost_adjusted_validation_robustness_service import (
    CostAdjustedValidationRobustnessResult,
    CostAdjustedValidationRobustnessScenarioResult,
    CostAdjustedValidationRobustnessService,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    CostAdjustedWalkForwardScenarioSummary,
    CostAdjustedWalkForwardValidationResult,
    CostAdjustedWalkForwardValidationService,
)
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DEFAULT_STEP,
    DeterministicRankingCandidateGenerationResult,
    DeterministicRankingCandidateGeneratorService,
    GeneratedRankingCandidate,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    InvalidForwardCandidateProvenance,
    aware_utc,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
    restore_weights,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioComparisonResult,
    RankingScenarioEvaluationMatrix,
    RankingScenarioSweepResult,
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.ranking_validation_robustness_service import (
    RankingValidationRobustnessResult,
    RobustnessDataError,
    RankingValidationRobustnessScenarioResult,
    RankingValidationRobustnessService,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    RankingWalkForwardScenarioSummary,
    RankingWalkForwardValidationResult,
    RankingWalkForwardValidationService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import policy_signature
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    TemporalRankingTurnoverResult,
    TemporalRankingTurnoverScenarioSummary,
    TemporalRankingTurnoverService,
)


REPORT_TYPE = "REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH"
BATCH_SCHEMA_VERSION = "reference-bounded-historical-research-batch-v1"
RESEARCH_PROFILE_SCHEMA_VERSION = "historical-research-batch-profile-v1"
CANONICAL_HORIZONS = (60, 240, 1440)
CANONICAL_INITIAL_RESEARCH_SIZE = 2
CANONICAL_VALIDATION_SIZE = 1
CANONICAL_FEE_RATE = Decimal("0.0005")
CANONICAL_SPREAD_COST_RATE = Decimal("0.0005")
CANONICAL_SLIPPAGE_RATE = Decimal("0.001")
SUCCESS = "SUCCESS"
NO_NOVEL_CANDIDATES = "NO_NOVEL_CANDIDATES"
NO_HISTORICAL_CONTEXT_SNAPSHOTS = "NO_HISTORICAL_CONTEXT_SNAPSHOTS"
INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH = (
    "INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH"
)


class _InvalidBatchData(Exception):
    pass


@dataclass(frozen=True)
class HistoricalResearchProfile:
    schema_version: str
    horizons: tuple[int, ...]
    initial_research_size: int
    validation_size: int
    assumptions: CostAssumptions


@dataclass(frozen=True)
class HistoricalResearchHorizonEvidence:
    horizon_minutes: int
    baseline_policy_signature: str
    effective_top_n: int
    gross_status: str
    gross_safe_reason: str | None
    gross: RankingScenarioComparisonResult | None
    gross_walk_forward_status: str
    gross_walk_forward_safe_reason: str | None
    gross_walk_forward: RankingWalkForwardScenarioSummary | None
    gross_robustness_status: str
    gross_robustness_safe_reason: str | None
    gross_robustness: RankingValidationRobustnessScenarioResult | None
    cost_adjusted_status: str
    cost_adjusted_safe_reason: str | None
    cost_adjusted: CostAdjustedRankingScenarioResult | None
    cost_walk_forward_status: str
    cost_walk_forward_safe_reason: str | None
    cost_walk_forward: CostAdjustedWalkForwardScenarioSummary | None
    cost_robustness_status: str
    cost_robustness_safe_reason: str | None
    cost_robustness: CostAdjustedValidationRobustnessScenarioResult | None


@dataclass(frozen=True)
class HistoricalResearchCandidateResult:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    donor_field: str
    receiver_field: str
    transfer_step: Decimal
    reference_snapshot_id: int
    reference_policy_signature: str
    already_registered: bool
    turnover_status: str
    turnover_safe_reason: str | None
    turnover_transition_count: int
    turnover_continuity_break_count: int
    turnover: TemporalRankingTurnoverScenarioSummary | None
    horizons: tuple[HistoricalResearchHorizonEvidence, ...]


@dataclass(frozen=True)
class ReferenceBoundedHistoricalResearchBatchResult:
    report_type: str
    batch_schema_version: str
    research_profile: HistoricalResearchProfile
    reference_snapshot_id: int
    reference_snapshot_captured_at: datetime | None
    historical_evidence_as_of: datetime | None
    user_id: int | None
    exchange: str | None
    quote_asset: str | None
    dataset_schema_version: str | None
    reference_policy_signature: str | None
    effective_top_n: int | None
    generator_schema_version: str | None
    generator_step: Decimal
    generated_candidate_count: int
    already_registered_candidate_count: int
    novel_candidate_count: int
    evaluated_candidate_count: int
    historical_timeline_snapshot_count: int
    historical_timeline_snapshot_ids: tuple[int, ...]
    historical_context_snapshot_count: int
    historical_context_snapshot_ids: tuple[int, ...]
    generator: DeterministicRankingCandidateGenerationResult | None
    gross_matrix: RankingScenarioEvaluationMatrix | None
    gross_sweep: RankingScenarioSweepResult | None
    gross_walk_forward: RankingWalkForwardValidationResult | None
    gross_robustness: RankingValidationRobustnessResult | None
    turnover: TemporalRankingTurnoverResult | None
    cost_adjusted: CostAdjustedRankingEvaluationResult | None
    cost_walk_forward: CostAdjustedWalkForwardValidationResult | None
    cost_robustness: CostAdjustedValidationRobustnessResult | None
    candidate_results: tuple[HistoricalResearchCandidateResult, ...]
    status: str
    safe_reason: str | None
    research_only: bool = True
    candidate_generation_performed: bool = True
    historical_evaluation_performed: bool = True
    automatic_policy_selection: bool = False
    policy_decision_performed: bool = False
    candidate_registration_performed: bool = False
    forward_enrollment_performed: bool = False
    promotion_performed: bool = False
    shadow_policy_created: bool = False
    full_live_activation_performed: bool = False
    database_write: bool = False
    external_calls: bool = False
    live_policy_change: bool = False
    live_order_change: bool = False
    reference_time_bounded: bool = True
    post_reference_snapshots_excluded: bool = True
    post_reference_outcomes_excluded: bool = True
    snapshot_id_ceiling_enforced: bool = True
    snapshot_captured_at_ceiling_enforced: bool = True
    snapshot_created_at_cutoff_enforced: bool = True
    outcome_evaluated_at_cutoff_enforced: bool = True
    outcome_created_at_cutoff_enforced: bool = True
    outcome_updated_at_cutoff_enforced: bool = True
    statistical_inference_performed: bool = False
    sample_sufficiency_assessed: bool = False


class ReferenceBoundedHistoricalResearchBatchService:
    """Aggregate all novel generated candidates using one bounded research graph."""

    def __init__(
        self,
        session: Session,
        *,
        generator_service: DeterministicRankingCandidateGeneratorService | None = None,
        sweep_service: RankingScenarioSweepService | None = None,
        gross_walk_forward_service: RankingWalkForwardValidationService | None = None,
        gross_robustness_service: RankingValidationRobustnessService | None = None,
        turnover_service: TemporalRankingTurnoverService | None = None,
        cost_service: CostAdjustedRankingEvaluationService | None = None,
        cost_walk_forward_service: CostAdjustedWalkForwardValidationService
        | None = None,
        cost_robustness_service: CostAdjustedValidationRobustnessService | None = None,
    ) -> None:
        self.session = session
        self.generator_service = generator_service or (
            DeterministicRankingCandidateGeneratorService(session)
        )
        self.sweep_service = sweep_service or RankingScenarioSweepService(session)
        self.gross_walk_forward_service = gross_walk_forward_service or (
            RankingWalkForwardValidationService(
                session, sweep_service=self.sweep_service
            )
        )
        self.gross_robustness_service = gross_robustness_service or (
            RankingValidationRobustnessService(
                session,
                sweep_service=self.sweep_service,
                walk_forward_service=self.gross_walk_forward_service,
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
        self.cost_walk_forward_service = cost_walk_forward_service or (
            CostAdjustedWalkForwardValidationService(
                session, cost_adjusted_service=self.cost_service
            )
        )
        self.cost_robustness_service = cost_robustness_service or (
            CostAdjustedValidationRobustnessService(
                session,
                cost_adjusted_service=self.cost_service,
                walk_forward_service=self.cost_walk_forward_service,
            )
        )
        self.profile = HistoricalResearchProfile(
            schema_version=RESEARCH_PROFILE_SCHEMA_VERSION,
            horizons=CANONICAL_HORIZONS,
            initial_research_size=CANONICAL_INITIAL_RESEARCH_SIZE,
            validation_size=CANONICAL_VALIDATION_SIZE,
            assumptions=build_cost_assumptions(
                fee_rate=CANONICAL_FEE_RATE,
                spread_cost_rate=CANONICAL_SPREAD_COST_RATE,
                slippage_rate=CANONICAL_SLIPPAGE_RATE,
            ),
        )

    def evaluate(
        self,
        *,
        reference_snapshot_id: int,
        step: Decimal | str = DEFAULT_STEP,
    ) -> ReferenceBoundedHistoricalResearchBatchResult:
        generator = None
        try:
            generator = self.generator_service.generate(
                reference_snapshot_id=reference_snapshot_id,
                step=step,
            )
            reference = self._reference_snapshot(reference_snapshot_id, generator)
            reference_captured_at = aware_utc(
                reference.captured_at, "reference snapshot captured_at"
            )
            as_of = aware_utc(reference.created_at, "reference snapshot created_at")
            novel = tuple(
                item for item in generator.all_candidates if not item.already_registered
            )
            if (
                len(generator.all_candidates) != generator.generated_valid_count
                or sum(item.already_registered for item in generator.all_candidates)
                != generator.already_registered_count
                or len(novel) != generator.novel_candidate_count
            ):
                raise _InvalidBatchData("generator candidate counts are inconsistent")
            if any(
                item.reference_snapshot_id != generator.reference_snapshot_id
                or item.reference_policy_signature
                != generator.reference_policy_signature
                or item.already_registered
                or item.scenario_name != item.definition.name
                or item.scenario_definition_signature
                != item.definition.definition_signature
                or item.component_weights != item.definition.component_weights
                for item in novel
            ):
                raise _InvalidBatchData("generated candidate identity is inconsistent")
            if not novel:
                return self._result(
                    generator,
                    reference_captured_at,
                    as_of,
                    (),
                    (),
                    status=NO_NOVEL_CANDIDATES,
                    historical_evaluation_performed=False,
                )
            timeline, context = self._load_and_validate_snapshots(
                reference, generator, reference_captured_at, as_of
            )
            if not context:
                return self._result(
                    generator,
                    reference_captured_at,
                    as_of,
                    timeline,
                    context,
                    status=NO_HISTORICAL_CONTEXT_SNAPSHOTS,
                    safe_reason="no compatible historical context snapshot exists",
                    historical_evaluation_performed=False,
                )
            scenarios = tuple(item.definition for item in novel)
            context_ids = tuple(item.id for item in context)
            timeline_ids = tuple(item.id for item in timeline)
            matrix = self.sweep_service.evaluate_matrix_snapshots(
                scenarios=scenarios,
                horizons=self.profile.horizons,
                snapshot_ids=context_ids,
                outcome_as_of=as_of,
            )
            gross_sweep = self.sweep_service.result_from_matrix(matrix)
            gross_walk = self.gross_walk_forward_service.evaluate_from_matrix(
                matrix,
                initial_research_size=self.profile.initial_research_size,
                validation_size=self.profile.validation_size,
            )
            gross_robustness = self.gross_robustness_service.evaluate_from_results(
                matrix, gross_walk
            )
            turnover = self.turnover_service.evaluate_snapshots(
                scenarios=scenarios, snapshot_ids=timeline_ids
            )
            assumptions = self.profile.assumptions
            cost = self.cost_service.evaluate_from_results(
                turnover,
                matrix,
                fee_rate=assumptions.fee_rate,
                spread_cost_rate=assumptions.spread_cost_rate,
                slippage_rate=assumptions.slippage_rate,
            )
            cost_walk = self.cost_walk_forward_service.evaluate_from_result(
                cost,
                initial_research_size=self.profile.initial_research_size,
                validation_size=self.profile.validation_size,
            )
            cost_robustness = self.cost_robustness_service.evaluate_from_results(
                cost, cost_walk
            )
            self._validate_top_level_identities(
                scenarios,
                matrix,
                gross_sweep,
                gross_walk,
                gross_robustness,
                turnover,
                cost,
                cost_walk,
                cost_robustness,
                generator,
                context_ids,
                timeline_ids,
            )
            candidate_results = self._aggregate(
                novel,
                generator,
                gross_sweep,
                gross_walk,
                gross_robustness,
                turnover,
                cost,
                cost_walk,
                cost_robustness,
            )
            self._validate_candidate_evidence(candidate_results)
            invalid_statuses = self._invalid_statuses(
                gross_sweep,
                gross_walk,
                gross_robustness,
                turnover,
                cost,
                cost_walk,
                cost_robustness,
            )
            if invalid_statuses:
                raise _InvalidBatchData(
                    "invalid upstream research data: " + ", ".join(invalid_statuses)
                )
            return self._result(
                generator,
                reference_captured_at,
                as_of,
                timeline,
                context,
                gross_matrix=matrix,
                gross_sweep=gross_sweep,
                gross_walk_forward=gross_walk,
                gross_robustness=gross_robustness,
                turnover=turnover,
                cost_adjusted=cost,
                cost_walk_forward=cost_walk,
                cost_robustness=cost_robustness,
                candidate_results=candidate_results,
                status=SUCCESS,
            )
        except (
            InvalidForwardCandidateProvenance,
            ReplayInputError,
            RobustnessDataError,
            _InvalidBatchData,
        ) as error:
            return self._invalid(reference_snapshot_id, step, generator, str(error))

    def _reference_snapshot(self, snapshot_id, generator):
        snapshot = self.session.scalar(
            select(StrategyReplaySnapshot)
            .where(StrategyReplaySnapshot.id == snapshot_id)
            .execution_options(autoflush=False)
        )
        if snapshot is None:
            raise _InvalidBatchData("reference snapshot does not exist")
        if (
            snapshot.id != generator.reference_snapshot_id
            or snapshot.user_id != generator.user_id
            or snapshot.exchange != generator.exchange
            or snapshot.quote_asset != generator.quote_asset
            or snapshot.dataset_schema_version != generator.dataset_schema_version
            or snapshot.policy_signature != generator.reference_policy_signature
        ):
            raise _InvalidBatchData(
                "reference snapshot identity does not match generator"
            )
        _, top_n = self._validated_snapshot_policy(snapshot)
        if top_n != generator.effective_top_n:
            raise _InvalidBatchData("reference effective TopN does not match generator")
        return snapshot

    def _load_and_validate_snapshots(self, reference, generator, captured_at, as_of):
        timeline = tuple(
            self.session.scalars(
                select(StrategyReplaySnapshot)
                .where(
                    StrategyReplaySnapshot.user_id == generator.user_id,
                    StrategyReplaySnapshot.exchange == generator.exchange,
                    StrategyReplaySnapshot.quote_asset == generator.quote_asset,
                    StrategyReplaySnapshot.dataset_schema_version
                    == generator.dataset_schema_version,
                    StrategyReplaySnapshot.id <= reference.id,
                    StrategyReplaySnapshot.captured_at <= captured_at,
                    StrategyReplaySnapshot.created_at <= as_of,
                )
                .order_by(
                    StrategyReplaySnapshot.captured_at.asc(),
                    StrategyReplaySnapshot.id.asc(),
                )
                .execution_options(autoflush=False)
            )
        )
        seen = set()
        previous = None
        context = []
        for snapshot in timeline:
            item_captured_at = aware_utc(snapshot.captured_at, "snapshot captured_at")
            item_created_at = aware_utc(snapshot.created_at, "snapshot created_at")
            key = (item_captured_at, snapshot.id)
            if snapshot.id in seen or (previous is not None and key <= previous):
                raise _InvalidBatchData("historical snapshot chronology is invalid")
            seen.add(snapshot.id)
            previous = key
            _, stored_top_n = self._validated_snapshot_policy(snapshot)
            if (
                snapshot.id > reference.id
                or item_captured_at > captured_at
                or item_created_at > as_of
            ):
                raise _InvalidBatchData(
                    "post-reference snapshot leaked into historical timeline"
                )
            if (
                snapshot.policy_signature == generator.reference_policy_signature
                and stored_top_n == generator.effective_top_n
            ):
                context.append(snapshot)
        return timeline, tuple(context)

    @staticmethod
    def _validated_snapshot_policy(snapshot):
        if not isinstance(snapshot.policy_data, dict):
            raise _InvalidBatchData("snapshot policy_data is invalid")
        if policy_signature(snapshot.policy_data) != snapshot.policy_signature:
            raise _InvalidBatchData(
                "snapshot policy signature does not match policy_data"
            )
        try:
            weights, top_n = restore_weights(snapshot.policy_data)
        except Exception as error:
            raise _InvalidBatchData(
                f"snapshot ranking policy is invalid: {error}"
            ) from error
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
            raise _InvalidBatchData("snapshot effective TopN is invalid")
        return weights, top_n

    def _validate_top_level_identities(
        self,
        scenarios,
        matrix,
        sweep,
        walk,
        robustness,
        turnover,
        cost,
        cost_walk,
        cost_robustness,
        generator,
        context_ids,
        timeline_ids,
    ):
        for name, value in (
            ("matrix", matrix),
            ("sweep", sweep),
            ("gross walk-forward", walk),
            ("gross robustness", robustness),
            ("turnover", turnover),
            ("cost-adjusted", cost),
            ("cost walk-forward", cost_walk),
            ("cost robustness", cost_robustness),
        ):
            if value.scenarios != scenarios:
                raise _InvalidBatchData(f"{name} scenario identity does not match")
        if (
            matrix.horizons != self.profile.horizons
            or matrix.requested_snapshot_count != len(context_ids)
            or matrix.evaluated_snapshot_count != len(context_ids)
            or sweep.requested_snapshot_count != len(context_ids)
            or turnover.requested_snapshot_count != len(timeline_ids)
        ):
            raise _InvalidBatchData("bounded snapshot lineage is invalid")
        matrix_cohorts = self._horizon_cohorts(
            matrix.cohorts,
            "gross matrix",
            generator.reference_policy_signature,
            generator.effective_top_n,
        )
        if any(
            tuple(
                snapshot_id
                for snapshot_id in matrix_cohorts[horizon].candidate_snapshot_ids
                if snapshot_id is not None
            )
            != context_ids
            for horizon in self.profile.horizons
        ):
            raise _InvalidBatchData("gross matrix context snapshot lineage is invalid")
        for value in (sweep, walk, robustness, cost, cost_walk, cost_robustness):
            if value.horizon_count != len(self.profile.horizons):
                raise _InvalidBatchData(
                    "historical horizon count does not match profile"
                )

    def _aggregate(
        self,
        novel,
        generator,
        sweep,
        walk,
        robustness,
        turnover,
        cost,
        cost_walk,
        cost_robustness,
    ):
        args = (generator.reference_policy_signature, generator.effective_top_n)
        sweep_by_horizon = self._horizon_cohorts(sweep.cohorts, "gross sweep", *args)
        walk_by_horizon = self._horizon_cohorts(
            walk.cohorts, "gross walk-forward", *args
        )
        robust_by_horizon = self._horizon_cohorts(
            robustness.cohorts, "gross robustness", *args
        )
        cost_by_horizon = self._horizon_cohorts(
            cost.cohorts, "cost-adjusted", *args, allow_empty=True
        )
        cost_walk_by_horizon = self._horizon_cohorts(
            cost_walk.cohorts, "cost walk-forward", *args, allow_empty=True
        )
        cost_robust_by_horizon = self._horizon_cohorts(
            cost_robustness.cohorts, "cost robustness", *args, allow_empty=True
        )
        turnover_cohort = self._turnover_cohort(turnover, generator)
        results = []
        for candidate in novel:
            turnover_summary = self._scenario_result(
                turnover_cohort.scenario_summaries if turnover_cohort else (),
                candidate,
                "turnover",
            )
            horizons = []
            for horizon in self.profile.horizons:
                gross_cohort = sweep_by_horizon[horizon]
                walk_cohort = walk_by_horizon[horizon]
                robust_cohort = robust_by_horizon[horizon]
                cost_cohort = cost_by_horizon[horizon]
                cost_walk_cohort = cost_walk_by_horizon[horizon]
                cost_robust_cohort = cost_robust_by_horizon[horizon]
                horizons.append(
                    HistoricalResearchHorizonEvidence(
                        horizon_minutes=horizon,
                        baseline_policy_signature=generator.reference_policy_signature,
                        effective_top_n=generator.effective_top_n,
                        gross_status=gross_cohort.status,
                        gross_safe_reason=gross_cohort.safe_reason,
                        gross=self._scenario_result(
                            gross_cohort.scenario_results, candidate, "gross"
                        ),
                        gross_walk_forward_status=walk_cohort.status,
                        gross_walk_forward_safe_reason=walk_cohort.safe_reason,
                        gross_walk_forward=self._scenario_result(
                            walk_cohort.scenario_results,
                            candidate,
                            "gross walk-forward",
                        ),
                        gross_robustness_status=robust_cohort.status,
                        gross_robustness_safe_reason=robust_cohort.safe_reason,
                        gross_robustness=self._scenario_result(
                            robust_cohort.scenario_results,
                            candidate,
                            "gross robustness",
                        ),
                        cost_adjusted_status=(
                            cost_cohort.status if cost_cohort else cost.status
                        ),
                        cost_adjusted_safe_reason=(
                            cost_cohort.safe_reason if cost_cohort else cost.safe_reason
                        ),
                        cost_adjusted=(
                            self._scenario_result(
                                cost_cohort.scenario_results,
                                candidate,
                                "cost-adjusted",
                            )
                            if cost_cohort
                            else None
                        ),
                        cost_walk_forward_status=(
                            cost_walk_cohort.status
                            if cost_walk_cohort
                            else cost_walk.status
                        ),
                        cost_walk_forward_safe_reason=(
                            cost_walk_cohort.safe_reason
                            if cost_walk_cohort
                            else cost_walk.safe_reason
                        ),
                        cost_walk_forward=(
                            self._scenario_result(
                                cost_walk_cohort.scenario_results,
                                candidate,
                                "cost walk-forward",
                            )
                            if cost_walk_cohort
                            else None
                        ),
                        cost_robustness_status=(
                            cost_robust_cohort.status
                            if cost_robust_cohort
                            else cost_robustness.status
                        ),
                        cost_robustness_safe_reason=(
                            cost_robust_cohort.safe_reason
                            if cost_robust_cohort
                            else cost_robustness.safe_reason
                        ),
                        cost_robustness=(
                            self._scenario_result(
                                cost_robust_cohort.scenario_results,
                                candidate,
                                "cost robustness",
                            )
                            if cost_robust_cohort
                            else None
                        ),
                    )
                )
            results.append(
                HistoricalResearchCandidateResult(
                    scenario_name=candidate.scenario_name,
                    scenario_definition_signature=(
                        candidate.scenario_definition_signature
                    ),
                    component_weights=dict(candidate.component_weights),
                    donor_field=candidate.donor_field,
                    receiver_field=candidate.receiver_field,
                    transfer_step=candidate.transfer_step,
                    reference_snapshot_id=candidate.reference_snapshot_id,
                    reference_policy_signature=candidate.reference_policy_signature,
                    already_registered=candidate.already_registered,
                    turnover_status=turnover.status,
                    turnover_safe_reason=turnover.safe_reason,
                    turnover_transition_count=(
                        turnover_cohort.transition_count if turnover_cohort else 0
                    ),
                    turnover_continuity_break_count=(
                        turnover_cohort.continuity_break_count if turnover_cohort else 0
                    ),
                    turnover=turnover_summary,
                    horizons=tuple(horizons),
                )
            )
        return tuple(results)

    def _horizon_cohorts(
        self,
        cohorts: Iterable[object],
        label: str,
        policy_signature: str,
        top_n: int,
        *,
        allow_empty: bool = False,
    ):
        indexed = {}
        for cohort in cohorts:
            key = (
                cohort.horizon_minutes,
                cohort.baseline_policy_signature,
                cohort.effective_top_n,
            )
            if key in indexed:
                raise _InvalidBatchData(f"duplicate {label} cohort identity")
            indexed[key] = cohort
        expected = {
            (horizon, policy_signature, top_n) for horizon in self.profile.horizons
        }
        if allow_empty and not indexed:
            return {horizon: None for horizon in self.profile.horizons}
        if set(indexed) != expected:
            raise _InvalidBatchData(f"unexpected or missing {label} cohort identity")
        return {key[0]: value for key, value in indexed.items()}

    @staticmethod
    def _scenario_result(values, candidate: GeneratedRankingCandidate, label):
        names = [item.scenario_name for item in values]
        if len(names) != len(set(names)):
            raise _InvalidBatchData(f"duplicate {label} scenario result")
        matches = tuple(
            item for item in values if item.scenario_name == candidate.scenario_name
        )
        if not matches:
            return None
        result = matches[0]
        if (
            result.scenario_definition_signature
            != candidate.scenario_definition_signature
        ):
            raise _InvalidBatchData(f"{label} candidate signature does not match")
        weights = getattr(result, "component_weights", None)
        if weights is not None and weights != candidate.component_weights:
            raise _InvalidBatchData(f"{label} candidate weights do not match")
        return result

    @staticmethod
    def _turnover_cohort(turnover, generator):
        matches = tuple(
            cohort
            for cohort in turnover.cohorts
            if cohort.baseline_policy_signature == generator.reference_policy_signature
            and cohort.effective_top_n == generator.effective_top_n
        )
        if len(matches) > 1:
            raise _InvalidBatchData("duplicate turnover cohort identity")
        if turnover.status == SUCCESS and not matches:
            raise _InvalidBatchData("successful turnover cohort identity is missing")
        return matches[0] if matches else None

    @staticmethod
    def _validate_candidate_evidence(candidate_results):
        for candidate in candidate_results:
            if candidate.turnover_status == SUCCESS and candidate.turnover is None:
                raise _InvalidBatchData(
                    "successful turnover candidate result is missing"
                )
            for evidence in candidate.horizons:
                for status, value, label in (
                    (evidence.gross_status, evidence.gross, "gross"),
                    (
                        evidence.gross_walk_forward_status,
                        evidence.gross_walk_forward,
                        "gross walk-forward",
                    ),
                    (
                        evidence.gross_robustness_status,
                        evidence.gross_robustness,
                        "gross robustness",
                    ),
                    (
                        evidence.cost_adjusted_status,
                        evidence.cost_adjusted,
                        "cost-adjusted",
                    ),
                    (
                        evidence.cost_walk_forward_status,
                        evidence.cost_walk_forward,
                        "cost walk-forward",
                    ),
                    (
                        evidence.cost_robustness_status,
                        evidence.cost_robustness,
                        "cost robustness",
                    ),
                ):
                    if status == SUCCESS and value is None:
                        raise _InvalidBatchData(
                            f"successful {label} candidate result is missing"
                        )

    @staticmethod
    def _invalid_statuses(*results):
        statuses = []
        for result in results:
            status = getattr(result, "status", None)
            if isinstance(status, str) and status.startswith("INVALID_"):
                statuses.append(status)
            for cohort in getattr(result, "cohorts", ()):
                cohort_status = getattr(cohort, "status", None)
                if isinstance(cohort_status, str) and cohort_status.startswith(
                    "INVALID_"
                ):
                    statuses.append(cohort_status)
        return tuple(dict.fromkeys(statuses))

    def _result(
        self,
        generator,
        captured_at,
        as_of,
        timeline,
        context,
        *,
        status,
        safe_reason=None,
        historical_evaluation_performed=True,
        gross_matrix=None,
        gross_sweep=None,
        gross_walk_forward=None,
        gross_robustness=None,
        turnover=None,
        cost_adjusted=None,
        cost_walk_forward=None,
        cost_robustness=None,
        candidate_results=(),
    ):
        return ReferenceBoundedHistoricalResearchBatchResult(
            report_type=REPORT_TYPE,
            batch_schema_version=BATCH_SCHEMA_VERSION,
            research_profile=self.profile,
            reference_snapshot_id=generator.reference_snapshot_id,
            reference_snapshot_captured_at=captured_at,
            historical_evidence_as_of=as_of,
            user_id=generator.user_id,
            exchange=generator.exchange,
            quote_asset=generator.quote_asset,
            dataset_schema_version=generator.dataset_schema_version,
            reference_policy_signature=generator.reference_policy_signature,
            effective_top_n=generator.effective_top_n,
            generator_schema_version=generator.generator_schema_version,
            generator_step=generator.step,
            generated_candidate_count=generator.generated_valid_count,
            already_registered_candidate_count=generator.already_registered_count,
            novel_candidate_count=generator.novel_candidate_count,
            evaluated_candidate_count=len(candidate_results),
            historical_timeline_snapshot_count=len(timeline),
            historical_timeline_snapshot_ids=tuple(item.id for item in timeline),
            historical_context_snapshot_count=len(context),
            historical_context_snapshot_ids=tuple(item.id for item in context),
            generator=generator,
            gross_matrix=gross_matrix,
            gross_sweep=gross_sweep,
            gross_walk_forward=gross_walk_forward,
            gross_robustness=gross_robustness,
            turnover=turnover,
            cost_adjusted=cost_adjusted,
            cost_walk_forward=cost_walk_forward,
            cost_robustness=cost_robustness,
            candidate_results=candidate_results,
            status=status,
            safe_reason=safe_reason,
            historical_evaluation_performed=historical_evaluation_performed,
        )

    def _invalid(self, reference_snapshot_id, step, generator, reason):
        try:
            normalized_step = Decimal(str(step))
        except Exception:
            normalized_step = DEFAULT_STEP
        return ReferenceBoundedHistoricalResearchBatchResult(
            report_type=REPORT_TYPE,
            batch_schema_version=BATCH_SCHEMA_VERSION,
            research_profile=self.profile,
            reference_snapshot_id=reference_snapshot_id,
            reference_snapshot_captured_at=None,
            historical_evidence_as_of=None,
            user_id=getattr(generator, "user_id", None),
            exchange=getattr(generator, "exchange", None),
            quote_asset=getattr(generator, "quote_asset", None),
            dataset_schema_version=getattr(generator, "dataset_schema_version", None),
            reference_policy_signature=getattr(
                generator, "reference_policy_signature", None
            ),
            effective_top_n=getattr(generator, "effective_top_n", None),
            generator_schema_version=getattr(
                generator, "generator_schema_version", None
            ),
            generator_step=normalized_step,
            generated_candidate_count=getattr(generator, "generated_valid_count", 0),
            already_registered_candidate_count=getattr(
                generator, "already_registered_count", 0
            ),
            novel_candidate_count=getattr(generator, "novel_candidate_count", 0),
            evaluated_candidate_count=0,
            historical_timeline_snapshot_count=0,
            historical_timeline_snapshot_ids=(),
            historical_context_snapshot_count=0,
            historical_context_snapshot_ids=(),
            generator=generator,
            gross_matrix=None,
            gross_sweep=None,
            gross_walk_forward=None,
            gross_robustness=None,
            turnover=None,
            cost_adjusted=None,
            cost_walk_forward=None,
            cost_robustness=None,
            candidate_results=(),
            status=INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH,
            safe_reason=reason,
            candidate_generation_performed=generator is not None,
            historical_evaluation_performed=False,
            reference_time_bounded=False,
            post_reference_snapshots_excluded=False,
            post_reference_outcomes_excluded=False,
            snapshot_id_ceiling_enforced=False,
            snapshot_captured_at_ceiling_enforced=False,
            snapshot_created_at_cutoff_enforced=False,
            outcome_evaluated_at_cutoff_enforced=False,
            outcome_created_at_cutoff_enforced=False,
            outcome_updated_at_cutoff_enforced=False,
        )


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "CANONICAL_HORIZONS",
    "CANONICAL_INITIAL_RESEARCH_SIZE",
    "CANONICAL_VALIDATION_SIZE",
    "INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH",
    "NO_HISTORICAL_CONTEXT_SNAPSHOTS",
    "NO_NOVEL_CANDIDATES",
    "RESEARCH_PROFILE_SCHEMA_VERSION",
    "REPORT_TYPE",
    "SUCCESS",
    "HistoricalResearchCandidateResult",
    "HistoricalResearchHorizonEvidence",
    "HistoricalResearchProfile",
    "ReferenceBoundedHistoricalResearchBatchResult",
    "ReferenceBoundedHistoricalResearchBatchService",
]
