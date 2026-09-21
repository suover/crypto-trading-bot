from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_COST_ADJUSTED_DATA,
    CostAdjustedRankingCohortResult,
    CostAdjustedRankingEvaluationResult,
    CostAdjustedRankingEvaluationService,
    CostAdjustedRankingSnapshotResult,
    CostAssumptions,
)
from crypto_trading_bot.services.cost_adjusted_walk_forward_validation_service import (
    INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA,
    INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
    NO_COST_ADJUSTABLE_SNAPSHOTS,
    SUCCESS,
    CostAdjustedWalkForwardCohortResult,
    CostAdjustedWalkForwardValidationResult,
    CostAdjustedWalkForwardValidationService,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioDefinition,
)
from crypto_trading_bot.services.ranking_validation_robustness_service import (
    RankingRobustnessDistributionStats,
    RobustnessDataError,
    distribution_statistics,
)


RESULT_TYPE = "DETERMINISTIC_COST_ADJUSTED_RANKING_VALIDATION_ROBUSTNESS_RESEARCH"
REPORT_TYPE = "COST_ADJUSTED_RANKING_VALIDATION_ROBUSTNESS"
ROBUSTNESS_STATISTICS_TYPE = "DESCRIPTIVE_ONLY"
INVALID_COST_ADJUSTED_ROBUSTNESS_DATA = "INVALID_COST_ADJUSTED_ROBUSTNESS_DATA"


@dataclass(frozen=True)
class CostAdjustedValidationFoldRobustness:
    statistics: RankingRobustnessDistributionStats
    worst_fold_index: int | None
    best_fold_index: int | None


@dataclass(frozen=True)
class CostAdjustedValidationSnapshotRobustness:
    statistics: RankingRobustnessDistributionStats
    worst_snapshot_id: int | None
    best_snapshot_id: int | None


@dataclass(frozen=True)
class CostAdjustedValidationRobustnessScenarioResult:
    scenario_name: str
    scenario_definition_signature: str
    scenario_signature: str
    component_weights: dict[str, Decimal]
    fold_statistics: CostAdjustedValidationFoldRobustness
    snapshot_statistics: CostAdjustedValidationSnapshotRobustness


@dataclass(frozen=True)
class CostAdjustedValidationRobustnessCohortResult:
    horizon_minutes: int
    baseline_policy_signature: str | None
    effective_top_n: int
    candidate_ab_snapshot_count: int
    common_comparable_ab_snapshot_count: int
    cost_adjustable_snapshot_count: int
    cost_adjustable_coverage_rate: Decimal
    initial_research_size: int
    validation_size: int
    step_size: int
    fold_count: int
    validation_snapshot_count: int
    unused_tail_snapshot_count: int
    status: str
    safe_reason: str | None
    robustness_computed: bool
    scenario_results: tuple[CostAdjustedValidationRobustnessScenarioResult, ...]


@dataclass(frozen=True)
class CostAdjustedValidationRobustnessResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenario_count: int
    horizon_count: int
    cohort_count: int
    initial_research_size: int
    validation_size: int
    step_size: int
    assumptions: CostAssumptions
    status: str
    safe_reason: str | None
    policy_decision_performed: bool
    statistical_inference_performed: bool
    sample_sufficiency_assessed: bool
    strict_unseen_validation: str
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[CostAdjustedValidationRobustnessCohortResult, ...]


class CostAdjustedValidationRobustnessService:
    """Describe cost-adjusted validation deltas without recomputing costs."""

    def __init__(
        self,
        session: Session,
        *,
        cost_adjusted_service: CostAdjustedRankingEvaluationService | None = None,
        walk_forward_service: CostAdjustedWalkForwardValidationService | None = None,
    ) -> None:
        self.cost_adjusted_service = (
            cost_adjusted_service or CostAdjustedRankingEvaluationService(session)
        )
        self.walk_forward_service = (
            walk_forward_service
            or CostAdjustedWalkForwardValidationService(
                session, cost_adjusted_service=self.cost_adjusted_service
            )
        )

    def evaluate(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        latest: int,
        fee_rate: object,
        spread_cost_rate: object,
        slippage_rate: object,
        initial_research_size: int,
        validation_size: int,
    ) -> CostAdjustedValidationRobustnessResult:
        source = self.cost_adjusted_service.evaluate(
            scenarios=scenarios,
            horizons=horizons,
            latest=latest,
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )
        walk_forward = self.walk_forward_service.evaluate_from_result(
            source,
            initial_research_size=initial_research_size,
            validation_size=validation_size,
        )
        return self.evaluate_from_results(source, walk_forward)

    def evaluate_from_results(
        self,
        source: CostAdjustedRankingEvaluationResult,
        walk_forward: CostAdjustedWalkForwardValidationResult,
    ) -> CostAdjustedValidationRobustnessResult:
        try:
            self._validate_top_level(source, walk_forward)
            source_by_key = {self._source_key(item): item for item in source.cohorts}
            walk_by_key = {
                self._walk_forward_key(item): item for item in walk_forward.cohorts
            }
            if len(source_by_key) != len(source.cohorts):
                raise RobustnessDataError("duplicate source cohort identity")
            if len(walk_by_key) != len(walk_forward.cohorts):
                raise RobustnessDataError("duplicate walk-forward cohort identity")
            if set(source_by_key) != set(walk_by_key):
                raise RobustnessDataError("source and walk-forward cohorts differ")
            cohorts = tuple(
                self._cohort_result(
                    source_by_key[self._walk_forward_key(walk)],
                    walk,
                    source.scenarios,
                    source.assumptions,
                )
                for walk in walk_forward.cohorts
            )
        except RobustnessDataError as error:
            return self._top_level_result(
                source,
                walk_forward,
                (),
                status=INVALID_COST_ADJUSTED_ROBUSTNESS_DATA,
                safe_reason=str(error),
            )
        status = self._overall_status(cohorts)
        return self._top_level_result(
            source,
            walk_forward,
            cohorts,
            status=status,
            safe_reason=None if status == SUCCESS else self._overall_reason(status),
        )

    @staticmethod
    def _validate_top_level(
        source: CostAdjustedRankingEvaluationResult,
        walk_forward: CostAdjustedWalkForwardValidationResult,
    ) -> None:
        if source.status == INVALID_COST_ADJUSTED_DATA:
            raise RobustnessDataError(
                source.safe_reason or "upstream cost-adjusted evaluation is invalid"
            )
        if walk_forward.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA:
            raise RobustnessDataError(
                walk_forward.safe_reason or "upstream walk-forward data is invalid"
            )
        if (
            source.requested_snapshot_count != walk_forward.requested_snapshot_count
            or source.evaluated_snapshot_count != walk_forward.evaluated_snapshot_count
            or source.scenario_count != len(source.scenarios)
            or source.scenario_count != walk_forward.scenario_count
            or source.horizon_count != walk_forward.horizon_count
            or source.cohort_count != len(source.cohorts)
            or walk_forward.cohort_count != len(walk_forward.cohorts)
            or source.assumptions != walk_forward.assumptions
            or source.scenarios != walk_forward.scenarios
            or walk_forward.step_size != walk_forward.validation_size
            or walk_forward.initial_research_size < 1
            or walk_forward.validation_size < 1
        ):
            raise RobustnessDataError("source and walk-forward metadata differ")
        CostAdjustedValidationRobustnessService._validate_assumptions(
            source.assumptions
        )

    def _cohort_result(
        self,
        source: CostAdjustedRankingCohortResult,
        walk_forward: CostAdjustedWalkForwardCohortResult,
        scenarios: tuple[RankingScenarioDefinition, ...],
        assumptions: CostAssumptions,
    ) -> CostAdjustedValidationRobustnessCohortResult:
        try:
            self._validate_cohort_identity(source, walk_forward)
            if (
                len(source.scenario_results) != len(scenarios)
                or len(walk_forward.scenario_results) != len(scenarios)
                or any(
                    len(fold.scenario_results) != len(scenarios)
                    for fold in walk_forward.folds
                )
            ):
                raise RobustnessDataError("cohort scenario count is inconsistent")
            if walk_forward.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA:
                raise RobustnessDataError(
                    walk_forward.safe_reason or "upstream walk-forward data is invalid"
                )
            if walk_forward.status in {
                NO_COST_ADJUSTABLE_SNAPSHOTS,
                INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA,
            }:
                return self._cohort_output(
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
                    scenario,
                    source,
                    walk_forward,
                    validation_ids,
                    assumptions,
                )
                for scenario in scenarios
            )
            return self._cohort_output(
                walk_forward,
                status=SUCCESS,
                safe_reason=None,
                validation_snapshot_count=len(validation_ids),
                scenario_results=scenario_results,
            )
        except RobustnessDataError as error:
            return self._cohort_output(
                walk_forward,
                status=INVALID_COST_ADJUSTED_ROBUSTNESS_DATA,
                safe_reason=str(error),
                validation_snapshot_count=0,
                scenario_results=(),
            )

    @staticmethod
    def _validate_cohort_identity(
        source: CostAdjustedRankingCohortResult,
        walk_forward: CostAdjustedWalkForwardCohortResult,
    ) -> None:
        if (
            CostAdjustedValidationRobustnessService._source_key(source)
            != CostAdjustedValidationRobustnessService._walk_forward_key(walk_forward)
            or source.candidate_ab_snapshot_count
            != walk_forward.candidate_ab_snapshot_count
            or source.common_comparable_ab_snapshot_count
            != walk_forward.common_comparable_ab_snapshot_count
            or source.cost_adjustable_snapshot_count
            != walk_forward.cost_adjustable_snapshot_count
            or source.cost_adjustable_coverage_rate
            != walk_forward.cost_adjustable_coverage_rate
            or walk_forward.fold_count != len(walk_forward.folds)
            or walk_forward.step_size != walk_forward.validation_size
        ):
            raise RobustnessDataError("walk-forward cohort metadata is inconsistent")

    @staticmethod
    def _validation_ids(
        walk_forward: CostAdjustedWalkForwardCohortResult,
    ) -> tuple[int, ...]:
        values: list[int] = []
        if tuple(item.fold_index for item in walk_forward.folds) != tuple(
            range(1, len(walk_forward.folds) + 1)
        ):
            raise RobustnessDataError("validation fold indexes are invalid")
        for fold in walk_forward.folds:
            if (
                fold.validation_snapshot_count != len(fold.validation_snapshot_ids)
                or fold.validation_snapshot_count != walk_forward.validation_size
            ):
                raise RobustnessDataError("validation fold size is inconsistent")
            if set(fold.research_snapshot_ids) & set(fold.validation_snapshot_ids):
                raise RobustnessDataError("research and validation snapshots overlap")
            for snapshot_id in fold.validation_snapshot_ids:
                if (
                    isinstance(snapshot_id, bool)
                    or not isinstance(snapshot_id, int)
                    or snapshot_id < 1
                ):
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
        source: CostAdjustedRankingCohortResult,
        walk_forward: CostAdjustedWalkForwardCohortResult,
        validation_ids: tuple[int, ...],
        assumptions: CostAssumptions,
    ) -> CostAdjustedValidationRobustnessScenarioResult:
        source_matches = tuple(
            item
            for item in source.scenario_results
            if item.scenario_name == scenario.name
        )
        if len(source_matches) != 1:
            raise RobustnessDataError("source scenario result is missing or duplicated")
        source_scenario = source_matches[0]
        summary_matches = tuple(
            item
            for item in walk_forward.scenario_results
            if item.scenario_name == scenario.name
        )
        if (
            source_scenario.scenario_definition_signature
            != scenario.definition_signature
            or not source_scenario.scenario_signature
            or source_scenario.cost_adjusted_snapshot_count
            != len(source_scenario.snapshots)
            or source_scenario.cost_adjusted_snapshot_count
            != source.cost_adjustable_snapshot_count
        ):
            raise RobustnessDataError("source scenario identity is invalid")
        if (
            len(summary_matches) != 1
            or summary_matches[0].scenario_definition_signature
            != scenario.definition_signature
            or summary_matches[0].scenario_signature
            != source_scenario.scenario_signature
            or summary_matches[0].component_weights != scenario.component_weights
            or summary_matches[0].validation_fold_count != walk_forward.fold_count
            or summary_matches[0].validation_snapshot_count != len(validation_ids)
        ):
            raise RobustnessDataError("walk-forward scenario summary is invalid")

        fold_values: list[tuple[int, Decimal]] = []
        for fold in walk_forward.folds:
            matches = tuple(
                item
                for item in fold.scenario_results
                if item.scenario_name == scenario.name
            )
            if len(matches) != 1:
                raise RobustnessDataError("walk-forward scenario result is invalid")
            fold_scenario = matches[0]
            if (
                fold_scenario.scenario_definition_signature
                != scenario.definition_signature
                or fold_scenario.scenario_signature
                != source_scenario.scenario_signature
                or fold_scenario.research.snapshot_count != fold.research_snapshot_count
                or fold_scenario.validation.snapshot_count
                != fold.validation_snapshot_count
            ):
                raise RobustnessDataError("walk-forward scenario identity is invalid")
            delta = fold_scenario.validation.mean_cost_adjusted_return_delta
            self._finite_decimal(delta, "validation fold delta")
            fold_values.append((fold.fold_index, delta))

        indexed: dict[int, CostAdjustedRankingSnapshotResult] = {}
        for snapshot in source_scenario.snapshots:
            self._validate_snapshot(
                snapshot,
                source,
                scenario,
                source_scenario.scenario_signature,
                assumptions,
            )
            if snapshot.snapshot_id in indexed:
                raise RobustnessDataError("source scenario has duplicate snapshot IDs")
            indexed[snapshot.snapshot_id] = snapshot
        snapshot_values_with_time = []
        for snapshot_id in validation_ids:
            snapshot = indexed.get(snapshot_id)
            if snapshot is None:
                raise RobustnessDataError("validation snapshot is missing from source")
            snapshot_values_with_time.append(
                (
                    snapshot.snapshot_id,
                    snapshot.cost_adjusted_return_delta,
                    snapshot.captured_at.astimezone(UTC),
                )
            )
        ordered = sorted(snapshot_values_with_time, key=lambda item: (item[2], item[0]))
        if tuple(item[0] for item in ordered) != validation_ids:
            raise RobustnessDataError("validation snapshot chronology is inconsistent")
        snapshot_values = [(item[0], item[1]) for item in ordered]
        self._validate_cross_scenario_baselines(source, validation_ids, indexed)
        return CostAdjustedValidationRobustnessScenarioResult(
            scenario_name=scenario.name,
            scenario_definition_signature=scenario.definition_signature,
            scenario_signature=source_scenario.scenario_signature,
            component_weights=scenario.component_weights,
            fold_statistics=CostAdjustedValidationFoldRobustness(
                statistics=distribution_statistics(value for _, value in fold_values),
                worst_fold_index=self._extreme_key(fold_values, find_minimum=True),
                best_fold_index=self._extreme_key(fold_values, find_minimum=False),
            ),
            snapshot_statistics=CostAdjustedValidationSnapshotRobustness(
                statistics=distribution_statistics(
                    value for _, value in snapshot_values
                ),
                worst_snapshot_id=self._extreme_key(snapshot_values, find_minimum=True),
                best_snapshot_id=self._extreme_key(snapshot_values, find_minimum=False),
            ),
        )

    @staticmethod
    def _validate_snapshot(
        snapshot: CostAdjustedRankingSnapshotResult,
        cohort: CostAdjustedRankingCohortResult,
        scenario: RankingScenarioDefinition,
        scenario_signature: str,
        assumptions: CostAssumptions,
    ) -> None:
        if (
            isinstance(snapshot.snapshot_id, bool)
            or not isinstance(snapshot.snapshot_id, int)
            or snapshot.snapshot_id < 1
            or not snapshot.pipeline_run_id
            or not isinstance(snapshot.captured_at, datetime)
            or snapshot.captured_at.tzinfo is None
            or snapshot.captured_at.utcoffset() is None
            or snapshot.horizon_minutes != cohort.horizon_minutes
            or snapshot.baseline_policy_signature != cohort.baseline_policy_signature
            or snapshot.effective_top_n != cohort.effective_top_n
            or snapshot.scenario_name != scenario.name
            or snapshot.scenario_definition_signature != scenario.definition_signature
            or snapshot.scenario_signature != scenario_signature
        ):
            raise RobustnessDataError("source snapshot lineage is invalid")
        if (
            snapshot.fee_rate != assumptions.fee_rate
            or snapshot.spread_cost_rate != assumptions.spread_cost_rate
            or snapshot.slippage_rate != assumptions.slippage_rate
            or snapshot.total_cost_rate != assumptions.total_cost_rate
        ):
            raise RobustnessDataError("source snapshot cost assumptions differ")
        for value in (
            snapshot.baseline_replacement_rate,
            snapshot.baseline_gross_traded_notional_ratio,
            snapshot.baseline_execution_cost_percentage,
            snapshot.scenario_replacement_rate,
            snapshot.scenario_gross_traded_notional_ratio,
            snapshot.scenario_execution_cost_percentage,
            snapshot.baseline_gross_return,
            snapshot.scenario_gross_return,
            snapshot.gross_return_delta,
            snapshot.baseline_cost_adjusted_return,
            snapshot.scenario_cost_adjusted_return,
            snapshot.cost_adjusted_return_delta,
        ):
            CostAdjustedValidationRobustnessService._finite_decimal(
                value, "source snapshot metric"
            )

    @staticmethod
    def _validate_cross_scenario_baselines(
        cohort: CostAdjustedRankingCohortResult,
        validation_ids: tuple[int, ...],
        indexed: dict[int, CostAdjustedRankingSnapshotResult],
    ) -> None:
        reference = {
            snapshot_id: CostAdjustedValidationRobustnessService._baseline_identity(
                indexed[snapshot_id]
            )
            for snapshot_id in validation_ids
        }
        for other in cohort.scenario_results:
            other_index = {item.snapshot_id: item for item in other.snapshots}
            if set(other_index) != set(indexed):
                raise RobustnessDataError("scenario snapshot samples differ")
            for snapshot_id in validation_ids:
                if (
                    CostAdjustedValidationRobustnessService._baseline_identity(
                        other_index[snapshot_id]
                    )
                    != reference[snapshot_id]
                ):
                    raise RobustnessDataError("cross-scenario baseline lineage differs")

    @staticmethod
    def _baseline_identity(snapshot: CostAdjustedRankingSnapshotResult) -> tuple:
        return (
            snapshot.snapshot_id,
            snapshot.pipeline_run_id,
            snapshot.captured_at.astimezone(UTC),
            snapshot.horizon_minutes,
            snapshot.baseline_policy_signature,
            snapshot.effective_top_n,
            snapshot.baseline_replacement_rate,
            snapshot.baseline_gross_traded_notional_ratio,
            snapshot.baseline_execution_cost_percentage,
            snapshot.baseline_gross_return,
            snapshot.baseline_cost_adjusted_return,
        )

    @staticmethod
    def _validate_assumptions(assumptions: CostAssumptions) -> None:
        for value in (
            assumptions.fee_rate,
            assumptions.spread_cost_rate,
            assumptions.slippage_rate,
            assumptions.total_cost_rate,
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise RobustnessDataError("cost assumptions are invalid")

    @staticmethod
    def _finite_decimal(value: object, field_name: str) -> None:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise RobustnessDataError(f"{field_name} is invalid")

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
    def _source_key(
        item: CostAdjustedRankingCohortResult,
    ) -> tuple[int, str | None, int]:
        return (
            item.horizon_minutes,
            item.baseline_policy_signature,
            item.effective_top_n,
        )

    @staticmethod
    def _walk_forward_key(
        item: CostAdjustedWalkForwardCohortResult,
    ) -> tuple[int, str | None, int]:
        return (
            item.horizon_minutes,
            item.baseline_policy_signature,
            item.effective_top_n,
        )

    @staticmethod
    def _cohort_output(
        source: CostAdjustedWalkForwardCohortResult,
        *,
        status: str,
        safe_reason: str | None,
        validation_snapshot_count: int,
        scenario_results: tuple[CostAdjustedValidationRobustnessScenarioResult, ...],
    ) -> CostAdjustedValidationRobustnessCohortResult:
        return CostAdjustedValidationRobustnessCohortResult(
            horizon_minutes=source.horizon_minutes,
            baseline_policy_signature=source.baseline_policy_signature,
            effective_top_n=source.effective_top_n,
            candidate_ab_snapshot_count=source.candidate_ab_snapshot_count,
            common_comparable_ab_snapshot_count=(
                source.common_comparable_ab_snapshot_count
            ),
            cost_adjustable_snapshot_count=source.cost_adjustable_snapshot_count,
            cost_adjustable_coverage_rate=source.cost_adjustable_coverage_rate,
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

    @staticmethod
    def _overall_status(
        cohorts: tuple[CostAdjustedValidationRobustnessCohortResult, ...],
    ) -> str:
        if any(
            item.status == INVALID_COST_ADJUSTED_ROBUSTNESS_DATA for item in cohorts
        ):
            return INVALID_COST_ADJUSTED_ROBUSTNESS_DATA
        if any(item.status == SUCCESS for item in cohorts):
            return SUCCESS
        if any(
            item.status == INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA
            for item in cohorts
        ):
            return INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA
        return NO_COST_ADJUSTABLE_SNAPSHOTS

    @staticmethod
    def _overall_reason(status: str) -> str:
        return {
            NO_COST_ADJUSTABLE_SNAPSHOTS: "no cohort has cost-adjustable snapshots",
            INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA: (
                "no cohort fills one complete cost-adjusted validation fold"
            ),
            INVALID_COST_ADJUSTED_ROBUSTNESS_DATA: (
                "cost-adjusted robustness integrity failed"
            ),
        }[status]

    @staticmethod
    def _top_level_result(
        source: CostAdjustedRankingEvaluationResult,
        walk_forward: CostAdjustedWalkForwardValidationResult,
        cohorts: tuple[CostAdjustedValidationRobustnessCohortResult, ...],
        *,
        status: str,
        safe_reason: str | None,
    ) -> CostAdjustedValidationRobustnessResult:
        return CostAdjustedValidationRobustnessResult(
            requested_snapshot_count=source.requested_snapshot_count,
            evaluated_snapshot_count=source.evaluated_snapshot_count,
            scenario_count=source.scenario_count,
            horizon_count=source.horizon_count,
            cohort_count=len(cohorts),
            initial_research_size=walk_forward.initial_research_size,
            validation_size=walk_forward.validation_size,
            step_size=walk_forward.step_size,
            assumptions=source.assumptions,
            status=status,
            safe_reason=safe_reason,
            policy_decision_performed=False,
            statistical_inference_performed=False,
            sample_sufficiency_assessed=False,
            strict_unseen_validation=walk_forward.strict_unseen_validation,
            scenarios=source.scenarios,
            cohorts=cohorts,
        )


__all__ = [
    "COST_ADJUSTED_METRIC_TYPE",
    "GROSS_PERFORMANCE_METRIC_TYPE",
    "INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA",
    "INVALID_COST_ADJUSTED_ROBUSTNESS_DATA",
    "NO_COST_ADJUSTABLE_SNAPSHOTS",
    "REPORT_TYPE",
    "RESULT_TYPE",
    "ROBUSTNESS_STATISTICS_TYPE",
    "SUCCESS",
    "CostAdjustedValidationFoldRobustness",
    "CostAdjustedValidationRobustnessCohortResult",
    "CostAdjustedValidationRobustnessResult",
    "CostAdjustedValidationRobustnessScenarioResult",
    "CostAdjustedValidationRobustnessService",
    "CostAdjustedValidationSnapshotRobustness",
    "RankingRobustnessDistributionStats",
    "RobustnessDataError",
    "distribution_statistics",
]
