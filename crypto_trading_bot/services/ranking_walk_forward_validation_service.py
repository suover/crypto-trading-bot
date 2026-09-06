from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_holdout_validation_service import (
    RankingHoldoutPeriodAggregate,
    ordered_common_results,
    summarize_period_results,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    INVALID_SWEEP_DATA,
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioComparableCohort,
    RankingScenarioDefinition,
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)


RESULT_TYPE = "EXPANDING_WINDOW_RANKING_WALK_FORWARD_RESEARCH"
PERFORMANCE_METRIC_TYPE = "RANKING_SELECTION_GROSS_MARKET_PERFORMANCE"
INSUFFICIENT_WALK_FORWARD_DATA = "INSUFFICIENT_WALK_FORWARD_DATA"
INVALID_WALK_FORWARD_DATA = "INVALID_WALK_FORWARD_DATA"


class WalkForwardInputError(ReplayInputError):
    pass


@dataclass(frozen=True)
class RankingWalkForwardScenarioFoldResult:
    scenario_name: str
    scenario_definition_signature: str
    research: RankingHoldoutPeriodAggregate
    validation: RankingHoldoutPeriodAggregate


@dataclass(frozen=True)
class RankingWalkForwardFoldResult:
    fold_index: int
    research_snapshot_count: int
    validation_snapshot_count: int
    research_start_at: datetime
    research_end_at: datetime
    validation_start_at: datetime
    validation_end_at: datetime
    research_snapshot_ids: tuple[int, ...]
    validation_snapshot_ids: tuple[int, ...]
    scenario_results: tuple[RankingWalkForwardScenarioFoldResult, ...]


@dataclass(frozen=True)
class RankingWalkForwardScenarioSummary:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    validation_fold_count: int
    positive_validation_fold_count: int
    negative_validation_fold_count: int
    tie_validation_fold_count: int
    mean_validation_return_delta: Decimal | None
    median_validation_return_delta: Decimal | None


@dataclass(frozen=True)
class RankingWalkForwardCohortResult:
    horizon_minutes: int
    baseline_policy_signature: str | None
    effective_top_n: int
    candidate_snapshot_count: int
    common_comparable_snapshot_count: int
    common_coverage_rate: Decimal
    initial_research_size: int
    validation_size: int
    step_size: int
    fold_count: int
    unused_tail_snapshot_count: int
    status: str
    safe_reason: str | None
    performance_compared: bool
    folds: tuple[RankingWalkForwardFoldResult, ...]
    scenario_results: tuple[RankingWalkForwardScenarioSummary, ...]


@dataclass(frozen=True)
class RankingWalkForwardValidationResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenario_count: int
    horizon_count: int
    cohort_count: int
    initial_research_size: int
    validation_size: int
    step_size: int
    policy_decision_performed: bool
    strict_unseen_validation: str
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[RankingWalkForwardCohortResult, ...]


class RankingWalkForwardValidationService:
    """Run expanding-window folds over shared comparable scenario results."""

    def __init__(
        self,
        session: Session,
        *,
        sweep_service: RankingScenarioSweepService | None = None,
        performance_service: StrategyABPerformanceService | None = None,
    ) -> None:
        self.performance_service = performance_service or (
            sweep_service.performance_service
            if sweep_service is not None
            else StrategyABPerformanceService(session)
        )
        self.sweep_service = sweep_service or RankingScenarioSweepService(
            session,
            performance_service=self.performance_service,
        )

    def evaluate(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        latest: int | None,
        initial_research_size: int,
        validation_size: int,
    ) -> RankingWalkForwardValidationResult:
        initial = self._positive_size(
            initial_research_size, field_name="initial research size"
        )
        validation = self._positive_size(validation_size, field_name="validation size")
        matrix = self.sweep_service.evaluate_matrix(
            scenarios=scenarios,
            horizons=horizons,
            latest=latest,
        )
        cohorts = tuple(
            self._evaluate_cohort(
                cohort,
                matrix.scenarios,
                initial_research_size=initial,
                validation_size=validation,
            )
            for cohort in matrix.cohorts
        )
        return RankingWalkForwardValidationResult(
            requested_snapshot_count=matrix.requested_snapshot_count,
            evaluated_snapshot_count=matrix.evaluated_snapshot_count,
            scenario_count=len(matrix.scenarios),
            horizon_count=len(matrix.horizons),
            cohort_count=len(cohorts),
            initial_research_size=initial,
            validation_size=validation,
            step_size=validation,
            policy_decision_performed=False,
            strict_unseen_validation="not_verified",
            scenarios=matrix.scenarios,
            cohorts=cohorts,
        )

    @staticmethod
    def _positive_size(value: int, *, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise WalkForwardInputError(f"{field_name} must be a positive integer")
        return value

    def _evaluate_cohort(
        self,
        cohort: RankingScenarioComparableCohort,
        scenarios: tuple[RankingScenarioDefinition, ...],
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> RankingWalkForwardCohortResult:
        ordered, chronology_error = ordered_common_results(cohort, scenarios)
        status = cohort.status
        safe_reason = cohort.safe_reason
        folds: tuple[RankingWalkForwardFoldResult, ...] = ()
        common_count = len(cohort.common_snapshot_ids)
        available_after_initial = max(common_count - initial_research_size, 0)
        full_fold_count = available_after_initial // validation_size
        unused_tail_count = available_after_initial % validation_size

        if status == INVALID_SWEEP_DATA:
            status = INVALID_WALK_FORWARD_DATA
        elif chronology_error is not None:
            status = INVALID_WALK_FORWARD_DATA
            safe_reason = chronology_error
        elif status == NO_COMMON_COMPARABLE_SNAPSHOTS:
            unused_tail_count = 0
        elif full_fold_count == 0:
            status = INSUFFICIENT_WALK_FORWARD_DATA
            safe_reason = (
                "common comparable snapshots do not fill one complete validation fold"
            )
        elif not self._all_common_results_present(cohort, scenarios):
            status = INVALID_WALK_FORWARD_DATA
            safe_reason = "common snapshot is missing from scenario results"
            unused_tail_count = 0
        else:
            folds = tuple(
                self._build_fold(
                    fold_offset,
                    ordered,
                    cohort,
                    scenarios,
                    initial_research_size=initial_research_size,
                    validation_size=validation_size,
                )
                for fold_offset in range(full_fold_count)
            )
            status = SUCCESS
            safe_reason = None

        scenario_results = self._scenario_summaries(scenarios, folds)
        return RankingWalkForwardCohortResult(
            horizon_minutes=cohort.horizon_minutes,
            baseline_policy_signature=cohort.baseline_policy_signature,
            effective_top_n=cohort.effective_top_n,
            candidate_snapshot_count=len(cohort.candidate_snapshot_ids),
            common_comparable_snapshot_count=common_count,
            common_coverage_rate=cohort.common_coverage_rate,
            initial_research_size=initial_research_size,
            validation_size=validation_size,
            step_size=validation_size,
            fold_count=len(folds),
            unused_tail_snapshot_count=unused_tail_count,
            status=status,
            safe_reason=safe_reason,
            performance_compared=status == SUCCESS,
            folds=folds,
            scenario_results=scenario_results,
        )

    @staticmethod
    def _all_common_results_present(
        cohort: RankingScenarioComparableCohort,
        scenarios: tuple[RankingScenarioDefinition, ...],
    ) -> bool:
        common = set(cohort.common_snapshot_ids)
        return all(
            common
            <= {result.snapshot_id for result in cohort.results_for(scenario.name)}
            for scenario in scenarios
        )

    def _build_fold(
        self,
        fold_offset: int,
        ordered: tuple[StrategyABSnapshotPerformanceResult, ...],
        cohort: RankingScenarioComparableCohort,
        scenarios: tuple[RankingScenarioDefinition, ...],
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> RankingWalkForwardFoldResult:
        research_end = initial_research_size + fold_offset * validation_size
        validation_end = research_end + validation_size
        research_rows = ordered[:research_end]
        validation_rows = ordered[research_end:validation_end]
        research_ids = tuple(result.snapshot_id for result in research_rows)
        validation_ids = tuple(result.snapshot_id for result in validation_rows)
        scenario_results = tuple(
            self._scenario_fold_result(
                scenario,
                cohort.results_for(scenario.name),
                research_ids,
                validation_ids,
            )
            for scenario in scenarios
        )
        return RankingWalkForwardFoldResult(
            fold_index=fold_offset + 1,
            research_snapshot_count=len(research_ids),
            validation_snapshot_count=len(validation_ids),
            research_start_at=research_rows[0].captured_at.astimezone(UTC),
            research_end_at=research_rows[-1].captured_at.astimezone(UTC),
            validation_start_at=validation_rows[0].captured_at.astimezone(UTC),
            validation_end_at=validation_rows[-1].captured_at.astimezone(UTC),
            research_snapshot_ids=research_ids,
            validation_snapshot_ids=validation_ids,
            scenario_results=scenario_results,
        )

    def _scenario_fold_result(
        self,
        scenario: RankingScenarioDefinition,
        results: tuple[StrategyABSnapshotPerformanceResult, ...],
        research_ids: tuple[int, ...],
        validation_ids: tuple[int, ...],
    ) -> RankingWalkForwardScenarioFoldResult:
        indexed = {result.snapshot_id: result for result in results}
        research = tuple(indexed[snapshot_id] for snapshot_id in research_ids)
        validation = tuple(indexed[snapshot_id] for snapshot_id in validation_ids)
        return RankingWalkForwardScenarioFoldResult(
            scenario_name=scenario.name,
            scenario_definition_signature=scenario.definition_signature,
            research=summarize_period_results(self.performance_service, research),
            validation=summarize_period_results(self.performance_service, validation),
        )

    @staticmethod
    def _scenario_summaries(
        scenarios: tuple[RankingScenarioDefinition, ...],
        folds: tuple[RankingWalkForwardFoldResult, ...],
    ) -> tuple[RankingWalkForwardScenarioSummary, ...]:
        summaries = []
        for scenario_index, scenario in enumerate(scenarios):
            deltas = tuple(
                fold.scenario_results[scenario_index].validation.mean_return_delta
                for fold in folds
            )
            if any(delta is None for delta in deltas):
                raise RuntimeError("successful validation fold lacks a mean delta")
            summaries.append(
                RankingWalkForwardScenarioSummary(
                    scenario_name=scenario.name,
                    scenario_definition_signature=scenario.definition_signature,
                    component_weights=scenario.component_weights,
                    validation_fold_count=len(deltas),
                    positive_validation_fold_count=sum(delta > 0 for delta in deltas),
                    negative_validation_fold_count=sum(delta < 0 for delta in deltas),
                    tie_validation_fold_count=sum(delta == 0 for delta in deltas),
                    mean_validation_return_delta=(
                        sum(deltas, Decimal("0")) / Decimal(len(deltas))
                        if deltas
                        else None
                    ),
                    median_validation_return_delta=median(deltas) if deltas else None,
                )
            )
        return tuple(summaries)
