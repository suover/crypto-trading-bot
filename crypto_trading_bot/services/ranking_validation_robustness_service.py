from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median, pstdev
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioComparableCohort,
    RankingScenarioDefinition,
    RankingScenarioEvaluationMatrix,
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    INSUFFICIENT_WALK_FORWARD_DATA,
    INVALID_WALK_FORWARD_DATA,
    RankingWalkForwardCohortResult,
    RankingWalkForwardValidationResult,
    RankingWalkForwardValidationService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    StrategyABPerformanceService,
)


RESULT_TYPE = "DETERMINISTIC_RANKING_VALIDATION_ROBUSTNESS_RESEARCH"
PERFORMANCE_METRIC_TYPE = "RANKING_SELECTION_GROSS_MARKET_PERFORMANCE"
ROBUSTNESS_STATISTICS_TYPE = "DESCRIPTIVE_ONLY"
INVALID_ROBUSTNESS_DATA = "INVALID_ROBUSTNESS_DATA"


class RobustnessDataError(ValueError):
    pass


@dataclass(frozen=True)
class RankingRobustnessDistributionStats:
    count: int
    positive_count: int
    negative_count: int
    tie_count: int
    positive_rate: Decimal | None
    mean_delta: Decimal | None
    median_delta: Decimal | None
    min_delta: Decimal | None
    max_delta: Decimal | None
    delta_range: Decimal | None
    delta_stddev: Decimal | None


@dataclass(frozen=True)
class RankingValidationFoldRobustness:
    statistics: RankingRobustnessDistributionStats
    worst_fold_index: int | None
    best_fold_index: int | None


@dataclass(frozen=True)
class RankingValidationSnapshotRobustness:
    statistics: RankingRobustnessDistributionStats
    worst_snapshot_id: int | None
    best_snapshot_id: int | None


@dataclass(frozen=True)
class RankingValidationRobustnessScenarioResult:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    fold_statistics: RankingValidationFoldRobustness
    snapshot_statistics: RankingValidationSnapshotRobustness


@dataclass(frozen=True)
class RankingValidationRobustnessCohortResult:
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
    validation_snapshot_count: int
    unused_tail_snapshot_count: int
    status: str
    safe_reason: str | None
    robustness_computed: bool
    scenario_results: tuple[RankingValidationRobustnessScenarioResult, ...]


@dataclass(frozen=True)
class RankingValidationRobustnessResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenario_count: int
    horizon_count: int
    cohort_count: int
    initial_research_size: int
    validation_size: int
    step_size: int
    policy_decision_performed: bool
    statistical_inference_performed: bool
    sample_sufficiency_assessed: bool
    strict_unseen_validation: str
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[RankingValidationRobustnessCohortResult, ...]


def distribution_statistics(
    values: Iterable[Decimal],
) -> RankingRobustnessDistributionStats:
    """Summarize an observed Decimal distribution without inference."""
    items = tuple(values)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in items):
        raise RobustnessDataError("distribution contains an invalid delta")
    count = len(items)
    if count == 0:
        return RankingRobustnessDistributionStats(
            count=0,
            positive_count=0,
            negative_count=0,
            tie_count=0,
            positive_rate=None,
            mean_delta=None,
            median_delta=None,
            min_delta=None,
            max_delta=None,
            delta_range=None,
            delta_stddev=None,
        )
    minimum = min(items)
    maximum = max(items)
    positive_count = sum(value > 0 for value in items)
    return RankingRobustnessDistributionStats(
        count=count,
        positive_count=positive_count,
        negative_count=sum(value < 0 for value in items),
        tie_count=sum(value == 0 for value in items),
        positive_rate=Decimal(positive_count) / Decimal(count),
        mean_delta=sum(items, Decimal("0")) / Decimal(count),
        median_delta=median(items),
        min_delta=minimum,
        max_delta=maximum,
        delta_range=maximum - minimum,
        delta_stddev=pstdev(items),
    )


class RankingValidationRobustnessService:
    """Describe walk-forward validation results without selecting a policy."""

    def __init__(
        self,
        session: Session,
        *,
        sweep_service: RankingScenarioSweepService | None = None,
        walk_forward_service: RankingWalkForwardValidationService | None = None,
        performance_service: StrategyABPerformanceService | None = None,
    ) -> None:
        performance = performance_service or (
            sweep_service.performance_service
            if sweep_service is not None
            else StrategyABPerformanceService(session)
        )
        self.sweep_service = sweep_service or RankingScenarioSweepService(
            session, performance_service=performance
        )
        self.walk_forward_service = (
            walk_forward_service
            or RankingWalkForwardValidationService(
                session,
                sweep_service=self.sweep_service,
                performance_service=performance,
            )
        )

    def evaluate(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        latest: int | None,
        initial_research_size: int,
        validation_size: int,
    ) -> RankingValidationRobustnessResult:
        matrix = self.sweep_service.evaluate_matrix(
            scenarios=scenarios,
            horizons=horizons,
            latest=latest,
        )
        return self.evaluate_from_matrix(
            matrix,
            initial_research_size=initial_research_size,
            validation_size=validation_size,
        )

    def evaluate_from_matrix(
        self,
        matrix: RankingScenarioEvaluationMatrix,
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> RankingValidationRobustnessResult:
        walk_forward = self.walk_forward_service.evaluate_from_matrix(
            matrix,
            initial_research_size=initial_research_size,
            validation_size=validation_size,
        )
        return self.evaluate_from_results(matrix, walk_forward)

    def evaluate_from_results(
        self,
        matrix: RankingScenarioEvaluationMatrix,
        walk_forward: RankingWalkForwardValidationResult,
    ) -> RankingValidationRobustnessResult:
        """Describe one already-computed matrix and walk-forward result."""
        if len(matrix.cohorts) != len(walk_forward.cohorts):
            raise RobustnessDataError("walk-forward cohort count does not match matrix")
        cohorts = tuple(
            self._cohort_result(matrix_cohort, walk_forward_cohort, matrix.scenarios)
            for matrix_cohort, walk_forward_cohort in zip(
                matrix.cohorts, walk_forward.cohorts, strict=True
            )
        )
        return RankingValidationRobustnessResult(
            requested_snapshot_count=walk_forward.requested_snapshot_count,
            evaluated_snapshot_count=walk_forward.evaluated_snapshot_count,
            scenario_count=walk_forward.scenario_count,
            horizon_count=walk_forward.horizon_count,
            cohort_count=len(cohorts),
            initial_research_size=walk_forward.initial_research_size,
            validation_size=walk_forward.validation_size,
            step_size=walk_forward.step_size,
            policy_decision_performed=False,
            statistical_inference_performed=False,
            sample_sufficiency_assessed=False,
            strict_unseen_validation=walk_forward.strict_unseen_validation,
            scenarios=matrix.scenarios,
            cohorts=cohorts,
        )

    def _cohort_result(
        self,
        matrix_cohort: RankingScenarioComparableCohort,
        walk_forward: RankingWalkForwardCohortResult,
        scenarios: tuple[RankingScenarioDefinition, ...],
    ) -> RankingValidationRobustnessCohortResult:
        try:
            self._validate_cohort_identity(matrix_cohort, walk_forward)
            if walk_forward.status == INVALID_WALK_FORWARD_DATA:
                raise RobustnessDataError(
                    walk_forward.safe_reason or "upstream walk-forward data is invalid"
                )
            if walk_forward.status in {
                NO_COMMON_COMPARABLE_SNAPSHOTS,
                INSUFFICIENT_WALK_FORWARD_DATA,
            }:
                return self._result(
                    walk_forward,
                    status=walk_forward.status,
                    safe_reason=walk_forward.safe_reason,
                    validation_snapshot_count=0,
                    scenario_results=(),
                )
            if walk_forward.status != SUCCESS or not walk_forward.folds:
                raise RobustnessDataError(
                    "walk-forward status and folds are inconsistent"
                )
            validation_ids = self._validation_ids(walk_forward)
            scenario_results = tuple(
                self._scenario_result(
                    scenario, matrix_cohort, walk_forward, validation_ids
                )
                for scenario in scenarios
            )
            return self._result(
                walk_forward,
                status=SUCCESS,
                safe_reason=None,
                validation_snapshot_count=len(validation_ids),
                scenario_results=scenario_results,
            )
        except RobustnessDataError as error:
            return self._result(
                walk_forward,
                status=INVALID_ROBUSTNESS_DATA,
                safe_reason=str(error),
                validation_snapshot_count=0,
                scenario_results=(),
            )

    @staticmethod
    def _validate_cohort_identity(
        matrix: RankingScenarioComparableCohort,
        walk_forward: RankingWalkForwardCohortResult,
    ) -> None:
        matrix_key = (
            matrix.horizon_minutes,
            matrix.baseline_policy_signature,
            matrix.effective_top_n,
        )
        walk_forward_key = (
            walk_forward.horizon_minutes,
            walk_forward.baseline_policy_signature,
            walk_forward.effective_top_n,
        )
        if matrix_key != walk_forward_key:
            raise RobustnessDataError(
                "walk-forward cohort does not match matrix cohort"
            )
        if (
            len(matrix.candidate_snapshot_ids) != walk_forward.candidate_snapshot_count
            or len(matrix.common_snapshot_ids)
            != walk_forward.common_comparable_snapshot_count
            or matrix.common_coverage_rate != walk_forward.common_coverage_rate
            or len(walk_forward.folds) != walk_forward.fold_count
            or walk_forward.step_size != walk_forward.validation_size
        ):
            raise RobustnessDataError("walk-forward cohort metadata is inconsistent")

    @staticmethod
    def _validation_ids(
        walk_forward: RankingWalkForwardCohortResult,
    ) -> tuple[int, ...]:
        values: list[int] = []
        if tuple(fold.fold_index for fold in walk_forward.folds) != tuple(
            range(1, len(walk_forward.folds) + 1)
        ):
            raise RobustnessDataError("validation fold indexes are invalid")
        for fold in walk_forward.folds:
            if (
                fold.validation_snapshot_count != len(fold.validation_snapshot_ids)
                or fold.validation_snapshot_count != walk_forward.validation_size
            ):
                raise RobustnessDataError("validation fold size is inconsistent")
            for snapshot_id in fold.validation_snapshot_ids:
                if isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int):
                    raise RobustnessDataError("validation snapshot ID is invalid")
                values.append(snapshot_id)
        if len(values) != len(set(values)):
            raise RobustnessDataError(
                "validation snapshot appears in multiple walk-forward folds"
            )
        return tuple(values)

    def _scenario_result(
        self,
        scenario: RankingScenarioDefinition,
        matrix: RankingScenarioComparableCohort,
        walk_forward: RankingWalkForwardCohortResult,
        validation_ids: tuple[int, ...],
    ) -> RankingValidationRobustnessScenarioResult:
        fold_values: list[tuple[int, Decimal]] = []
        for fold in walk_forward.folds:
            matching = tuple(
                item
                for item in fold.scenario_results
                if item.scenario_name == scenario.name
                and item.scenario_definition_signature == scenario.definition_signature
            )
            if len(matching) != 1:
                raise RobustnessDataError(
                    "walk-forward scenario fold result is invalid"
                )
            delta = matching[0].validation.mean_return_delta
            if not isinstance(delta, Decimal) or not delta.is_finite():
                raise RobustnessDataError("validation fold delta is invalid")
            fold_values.append((fold.fold_index, delta))

        matrix_results = matrix.results_for(scenario.name)
        indexed = {result.snapshot_id: result for result in matrix_results}
        if len(indexed) != len(matrix_results):
            raise RobustnessDataError(
                "scenario matrix contains duplicate snapshot results"
            )
        if not set(validation_ids) <= set(matrix.common_snapshot_ids):
            raise RobustnessDataError("validation snapshot is outside the common set")
        snapshot_values_with_time: list[tuple[int, Decimal, datetime]] = []
        for snapshot_id in validation_ids:
            result = indexed.get(snapshot_id)
            if result is None:
                raise RobustnessDataError("validation snapshot result is missing")
            if (
                result.status != SUCCESS
                or result.performance_evaluated is not True
                or not isinstance(result.mean_return_delta, Decimal)
                or not result.mean_return_delta.is_finite()
            ):
                raise RobustnessDataError(
                    "validation snapshot result is not comparable"
                )
            if (
                isinstance(result.snapshot_id, bool)
                or not isinstance(result.snapshot_id, int)
                or not isinstance(result.captured_at, datetime)
                or result.captured_at.tzinfo is None
                or result.captured_at.utcoffset() is None
                or result.horizon_minutes != matrix.horizon_minutes
                or result.baseline_policy_signature != matrix.baseline_policy_signature
                or result.effective_top_n != matrix.effective_top_n
            ):
                raise RobustnessDataError("validation snapshot metadata is invalid")
            snapshot_values_with_time.append(
                (
                    snapshot_id,
                    result.mean_return_delta,
                    result.captured_at.astimezone(UTC),
                )
            )

        snapshot_values = [
            (snapshot_id, delta)
            for snapshot_id, delta, _ in sorted(
                snapshot_values_with_time, key=lambda item: (item[2], item[0])
            )
        ]

        fold_stats = distribution_statistics(value for _, value in fold_values)
        snapshot_stats = distribution_statistics(value for _, value in snapshot_values)
        return RankingValidationRobustnessScenarioResult(
            scenario_name=scenario.name,
            scenario_definition_signature=scenario.definition_signature,
            component_weights=scenario.component_weights,
            fold_statistics=RankingValidationFoldRobustness(
                statistics=fold_stats,
                worst_fold_index=self._extreme_key(fold_values, find_minimum=True),
                best_fold_index=self._extreme_key(fold_values, find_minimum=False),
            ),
            snapshot_statistics=RankingValidationSnapshotRobustness(
                statistics=snapshot_stats,
                worst_snapshot_id=self._extreme_key(snapshot_values, find_minimum=True),
                best_snapshot_id=self._extreme_key(snapshot_values, find_minimum=False),
            ),
        )

    @staticmethod
    def _extreme_key(
        keyed_values: list[tuple[int, Decimal]], *, find_minimum: bool
    ) -> int | None:
        if not keyed_values:
            return None
        extreme = (
            min(value for _, value in keyed_values)
            if find_minimum
            else max(value for _, value in keyed_values)
        )
        return next(key for key, value in keyed_values if value == extreme)

    @staticmethod
    def _result(
        source: RankingWalkForwardCohortResult,
        *,
        status: str,
        safe_reason: str | None,
        validation_snapshot_count: int,
        scenario_results: tuple[RankingValidationRobustnessScenarioResult, ...],
    ) -> RankingValidationRobustnessCohortResult:
        return RankingValidationRobustnessCohortResult(
            horizon_minutes=source.horizon_minutes,
            baseline_policy_signature=source.baseline_policy_signature,
            effective_top_n=source.effective_top_n,
            candidate_snapshot_count=source.candidate_snapshot_count,
            common_comparable_snapshot_count=source.common_comparable_snapshot_count,
            common_coverage_rate=source.common_coverage_rate,
            initial_research_size=source.initial_research_size,
            validation_size=source.validation_size,
            step_size=source.step_size,
            fold_count=source.fold_count,
            validation_snapshot_count=validation_snapshot_count,
            unused_tail_snapshot_count=source.unused_tail_snapshot_count,
            status=status,
            safe_reason=safe_reason,
            robustness_computed=status == SUCCESS,
            scenario_results=scenario_results,
        )
