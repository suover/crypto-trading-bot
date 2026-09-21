from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    COST_ADJUSTED_METRIC_TYPE,
    GROSS_PERFORMANCE_METRIC_TYPE,
    INVALID_COST_ADJUSTED_DATA,
    NO_COMMON_COMPARABLE_SNAPSHOTS as COST_NO_COMMON_COMPARABLE_SNAPSHOTS,
    NO_COST_ADJUSTABLE_SNAPSHOTS as COST_NO_COST_ADJUSTABLE_SNAPSHOTS,
    NO_TURNOVER_TRANSITIONS as COST_NO_TURNOVER_TRANSITIONS,
    SUCCESS as COST_ADJUSTED_SUCCESS,
    CostAdjustedRankingCohortResult,
    CostAdjustedRankingEvaluationResult,
    CostAdjustedRankingEvaluationService,
    CostAdjustedRankingSnapshotResult,
    CostAssumptions,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioDefinition,
)


RESULT_TYPE = "EXPANDING_WINDOW_COST_ADJUSTED_RANKING_VALIDATION_RESEARCH"
REPORT_TYPE = "COST_ADJUSTED_RANKING_WALK_FORWARD"
SUCCESS = "SUCCESS"
NO_COST_ADJUSTABLE_SNAPSHOTS = "NO_COST_ADJUSTABLE_SNAPSHOTS"
INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA = (
    "INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA"
)
INVALID_COST_ADJUSTED_WALK_FORWARD_DATA = "INVALID_COST_ADJUSTED_WALK_FORWARD_DATA"


class CostAdjustedWalkForwardInputError(ReplayInputError):
    pass


class _InvalidCostAdjustedWalkForwardData(Exception):
    pass


@dataclass(frozen=True)
class CostAdjustedWalkForwardPeriodAggregate:
    snapshot_count: int
    mean_gross_return_delta: Decimal
    median_gross_return_delta: Decimal
    mean_cost_adjusted_return_delta: Decimal
    median_cost_adjusted_return_delta: Decimal
    positive_cost_adjusted_snapshot_count: int
    negative_cost_adjusted_snapshot_count: int
    tie_cost_adjusted_snapshot_count: int
    positive_cost_adjusted_snapshot_rate: Decimal


@dataclass(frozen=True)
class CostAdjustedWalkForwardScenarioFoldResult:
    scenario_name: str
    scenario_definition_signature: str
    scenario_signature: str
    research: CostAdjustedWalkForwardPeriodAggregate
    validation: CostAdjustedWalkForwardPeriodAggregate


@dataclass(frozen=True)
class CostAdjustedWalkForwardFoldResult:
    fold_index: int
    research_snapshot_count: int
    validation_snapshot_count: int
    research_start_at: datetime
    research_end_at: datetime
    validation_start_at: datetime
    validation_end_at: datetime
    research_snapshot_ids: tuple[int, ...]
    validation_snapshot_ids: tuple[int, ...]
    scenario_results: tuple[CostAdjustedWalkForwardScenarioFoldResult, ...]


@dataclass(frozen=True)
class CostAdjustedWalkForwardScenarioSummary:
    scenario_name: str
    scenario_definition_signature: str
    scenario_signature: str | None
    component_weights: dict[str, Decimal]
    validation_fold_count: int
    positive_validation_fold_count: int
    negative_validation_fold_count: int
    tie_validation_fold_count: int
    mean_validation_gross_return_delta: Decimal | None
    median_validation_gross_return_delta: Decimal | None
    mean_validation_cost_adjusted_return_delta: Decimal | None
    median_validation_cost_adjusted_return_delta: Decimal | None
    validation_snapshot_count: int
    positive_validation_snapshot_count: int
    negative_validation_snapshot_count: int
    tie_validation_snapshot_count: int
    positive_validation_snapshot_rate: Decimal | None


@dataclass(frozen=True)
class CostAdjustedWalkForwardCohortResult:
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
    unused_tail_snapshot_count: int
    status: str
    safe_reason: str | None
    performance_compared: bool
    folds: tuple[CostAdjustedWalkForwardFoldResult, ...]
    scenario_results: tuple[CostAdjustedWalkForwardScenarioSummary, ...]


@dataclass(frozen=True)
class CostAdjustedWalkForwardValidationResult:
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
    strict_unseen_validation: str
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[CostAdjustedWalkForwardCohortResult, ...]


class CostAdjustedWalkForwardValidationService:
    """Build chronological folds from one immutable cost-adjusted evaluation."""

    def __init__(
        self,
        session: Session,
        *,
        cost_adjusted_service: CostAdjustedRankingEvaluationService | None = None,
    ) -> None:
        self.cost_adjusted_service = (
            cost_adjusted_service or CostAdjustedRankingEvaluationService(session)
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
    ) -> CostAdjustedWalkForwardValidationResult:
        initial = self._positive_size(
            initial_research_size, field_name="initial research size"
        )
        validation = self._positive_size(validation_size, field_name="validation size")
        source = self.cost_adjusted_service.evaluate(
            scenarios=scenarios,
            horizons=horizons,
            latest=latest,
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )
        return self._evaluate_validated_sizes(
            source,
            initial_research_size=initial,
            validation_size=validation,
        )

    def evaluate_from_result(
        self,
        source: CostAdjustedRankingEvaluationResult,
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> CostAdjustedWalkForwardValidationResult:
        initial = self._positive_size(
            initial_research_size, field_name="initial research size"
        )
        validation = self._positive_size(validation_size, field_name="validation size")
        return self._evaluate_validated_sizes(
            source,
            initial_research_size=initial,
            validation_size=validation,
        )

    @staticmethod
    def _positive_size(value: int, *, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise CostAdjustedWalkForwardInputError(
                f"{field_name} must be a positive integer"
            )
        return value

    def _evaluate_validated_sizes(
        self,
        source: CostAdjustedRankingEvaluationResult,
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> CostAdjustedWalkForwardValidationResult:
        if source.status == INVALID_COST_ADJUSTED_DATA:
            return self._result(
                source,
                initial_research_size,
                validation_size,
                (),
                INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
                f"upstream cost-adjusted evaluation invalid: {source.safe_reason}",
            )
        try:
            self._validate_source(source)
            evaluated_cohorts = []
            for cohort in source.cohorts:
                try:
                    evaluated = self._evaluate_cohort(
                        cohort,
                        source.scenarios,
                        source.assumptions,
                        initial_research_size=initial_research_size,
                        validation_size=validation_size,
                    )
                except _InvalidCostAdjustedWalkForwardData as error:
                    evaluated = self._empty_cohort(
                        cohort,
                        source.scenarios,
                        initial_research_size,
                        validation_size,
                        INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
                        str(error),
                        unused_tail=0,
                    )
                evaluated_cohorts.append(evaluated)
            cohorts = tuple(evaluated_cohorts)
        except _InvalidCostAdjustedWalkForwardData as error:
            return self._result(
                source,
                initial_research_size,
                validation_size,
                (),
                INVALID_COST_ADJUSTED_WALK_FORWARD_DATA,
                str(error),
            )
        status = self._overall_status(cohorts)
        reason = None if status == SUCCESS else self._overall_reason(status)
        return self._result(
            source,
            initial_research_size,
            validation_size,
            cohorts,
            status,
            reason,
        )

    @staticmethod
    def _validate_source(source: CostAdjustedRankingEvaluationResult) -> None:
        if (
            not isinstance(source.requested_snapshot_count, int)
            or isinstance(source.requested_snapshot_count, bool)
            or source.requested_snapshot_count < 0
            or not isinstance(source.evaluated_snapshot_count, int)
            or isinstance(source.evaluated_snapshot_count, bool)
            or source.evaluated_snapshot_count < 0
            or not isinstance(source.scenario_count, int)
            or isinstance(source.scenario_count, bool)
            or source.scenario_count != len(source.scenarios)
            or not isinstance(source.horizon_count, int)
            or isinstance(source.horizon_count, bool)
            or source.horizon_count < 1
            or not isinstance(source.cohort_count, int)
            or isinstance(source.cohort_count, bool)
            or source.cohort_count != len(source.cohorts)
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                "upstream result metadata is invalid"
            )
        scenario_names = tuple(item.name for item in source.scenarios)
        scenario_signatures = tuple(
            item.definition_signature for item in source.scenarios
        )
        if (
            not scenario_names
            or len(scenario_names) != len(set(scenario_names))
            or any(not name for name in scenario_names)
            or len(scenario_signatures) != len(set(scenario_signatures))
            or any(not signature for signature in scenario_signatures)
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                "upstream scenario definitions are invalid"
            )
        CostAdjustedWalkForwardValidationService._validate_assumptions(
            source.assumptions
        )
        allowed_statuses = {
            COST_ADJUSTED_SUCCESS,
            COST_NO_COMMON_COMPARABLE_SNAPSHOTS,
            COST_NO_COST_ADJUSTABLE_SNAPSHOTS,
            COST_NO_TURNOVER_TRANSITIONS,
        }
        if source.status not in allowed_statuses:
            raise _InvalidCostAdjustedWalkForwardData(
                "upstream cost-adjusted status is invalid"
            )
        cohort_keys: set[tuple[int, str | None, int]] = set()
        for cohort in source.cohorts:
            key = (
                cohort.horizon_minutes,
                cohort.baseline_policy_signature,
                cohort.effective_top_n,
            )
            if key in cohort_keys:
                raise _InvalidCostAdjustedWalkForwardData(
                    "duplicate cost-adjusted cohort identity"
                )
            cohort_keys.add(key)
        successful = any(
            cohort.status == COST_ADJUSTED_SUCCESS for cohort in source.cohorts
        )
        if (source.status == COST_ADJUSTED_SUCCESS) != successful:
            raise _InvalidCostAdjustedWalkForwardData(
                "upstream overall and cohort statuses are inconsistent"
            )
        if source.cohorts and (
            len({cohort.horizon_minutes for cohort in source.cohorts})
            != source.horizon_count
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                "upstream horizon metadata is inconsistent"
            )

    @staticmethod
    def _validate_assumptions(assumptions: CostAssumptions) -> None:
        values = (
            assumptions.fee_rate,
            assumptions.spread_cost_rate,
            assumptions.slippage_rate,
            assumptions.total_cost_rate,
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
            for value in values
        ):
            raise _InvalidCostAdjustedWalkForwardData("cost assumptions are invalid")

    def _evaluate_cohort(
        self,
        cohort: CostAdjustedRankingCohortResult,
        scenarios: tuple[RankingScenarioDefinition, ...],
        assumptions: CostAssumptions,
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> CostAdjustedWalkForwardCohortResult:
        self._validate_cohort_metadata(cohort)
        ordered, scenario_rows = self._validated_ordered_rows(
            cohort, scenarios, assumptions
        )
        count = len(ordered)
        if count == 0:
            return self._empty_cohort(
                cohort,
                scenarios,
                initial_research_size,
                validation_size,
                NO_COST_ADJUSTABLE_SNAPSHOTS,
                cohort.safe_reason or "cohort has no cost-adjustable snapshots",
                unused_tail=0,
            )
        if cohort.status != COST_ADJUSTED_SUCCESS:
            raise _InvalidCostAdjustedWalkForwardData(
                "non-success upstream cohort contains cost-adjustable snapshots"
            )
        available_after_initial = max(count - initial_research_size, 0)
        fold_count = available_after_initial // validation_size
        unused_tail = available_after_initial % validation_size
        if fold_count == 0:
            return self._empty_cohort(
                cohort,
                scenarios,
                initial_research_size,
                validation_size,
                INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA,
                "cost-adjustable snapshots do not fill one complete validation fold",
                unused_tail=unused_tail,
            )
        folds = tuple(
            self._build_fold(
                fold_offset,
                ordered,
                scenario_rows,
                scenarios,
                initial_research_size=initial_research_size,
                validation_size=validation_size,
            )
            for fold_offset in range(fold_count)
        )
        return CostAdjustedWalkForwardCohortResult(
            horizon_minutes=cohort.horizon_minutes,
            baseline_policy_signature=cohort.baseline_policy_signature,
            effective_top_n=cohort.effective_top_n,
            candidate_ab_snapshot_count=cohort.candidate_ab_snapshot_count,
            common_comparable_ab_snapshot_count=(
                cohort.common_comparable_ab_snapshot_count
            ),
            cost_adjustable_snapshot_count=count,
            cost_adjustable_coverage_rate=cohort.cost_adjustable_coverage_rate,
            initial_research_size=initial_research_size,
            validation_size=validation_size,
            step_size=validation_size,
            fold_count=len(folds),
            unused_tail_snapshot_count=unused_tail,
            status=SUCCESS,
            safe_reason=None,
            performance_compared=True,
            folds=folds,
            scenario_results=self._scenario_summaries(scenarios, folds),
        )

    @staticmethod
    def _validate_cohort_metadata(cohort: CostAdjustedRankingCohortResult) -> None:
        if (
            not isinstance(cohort.horizon_minutes, int)
            or isinstance(cohort.horizon_minutes, bool)
            or cohort.horizon_minutes < 1
            or not isinstance(cohort.baseline_policy_signature, str)
            or not cohort.baseline_policy_signature
            or not isinstance(cohort.effective_top_n, int)
            or isinstance(cohort.effective_top_n, bool)
            or cohort.effective_top_n < 1
            or not isinstance(cohort.cost_adjustable_snapshot_count, int)
            or isinstance(cohort.cost_adjustable_snapshot_count, bool)
            or cohort.cost_adjustable_snapshot_count < 0
            or not isinstance(cohort.candidate_ab_snapshot_count, int)
            or isinstance(cohort.candidate_ab_snapshot_count, bool)
            or not isinstance(cohort.common_comparable_ab_snapshot_count, int)
            or isinstance(cohort.common_comparable_ab_snapshot_count, bool)
            or cohort.candidate_ab_snapshot_count
            < cohort.common_comparable_ab_snapshot_count
            or cohort.common_comparable_ab_snapshot_count
            < cohort.cost_adjustable_snapshot_count
            or not isinstance(cohort.cost_adjustable_coverage_rate, Decimal)
            or not cohort.cost_adjustable_coverage_rate.is_finite()
            or cohort.cost_adjustable_coverage_rate < 0
            or cohort.cost_adjustable_coverage_rate > 1
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                "cost-adjusted cohort metadata is invalid"
            )

    def _validated_ordered_rows(
        self,
        cohort: CostAdjustedRankingCohortResult,
        scenarios: tuple[RankingScenarioDefinition, ...],
        assumptions: CostAssumptions,
    ) -> tuple[
        tuple[CostAdjustedRankingSnapshotResult, ...],
        dict[str, dict[int, CostAdjustedRankingSnapshotResult]],
    ]:
        if len(cohort.scenario_results) != len(scenarios):
            raise _InvalidCostAdjustedWalkForwardData(
                "cost-adjusted scenario count mismatch"
            )
        by_name = {item.scenario_name: item for item in cohort.scenario_results}
        if len(by_name) != len(cohort.scenario_results):
            raise _InvalidCostAdjustedWalkForwardData(
                "duplicate cost-adjusted scenario result"
            )
        indexed: dict[str, dict[int, CostAdjustedRankingSnapshotResult]] = {}
        expected_ids: set[int] | None = None
        first_rows: tuple[CostAdjustedRankingSnapshotResult, ...] = ()
        for scenario in scenarios:
            result = by_name.get(scenario.name)
            if (
                result is None
                or result.scenario_definition_signature != scenario.definition_signature
                or result.cost_adjusted_snapshot_count != len(result.snapshots)
                or result.cost_adjusted_snapshot_count
                != cohort.cost_adjustable_snapshot_count
            ):
                raise _InvalidCostAdjustedWalkForwardData(
                    f"scenario result identity mismatch: {scenario.name}"
                )
            rows: dict[int, CostAdjustedRankingSnapshotResult] = {}
            for snapshot in result.snapshots:
                self._validate_snapshot(
                    snapshot,
                    cohort,
                    scenario,
                    result.scenario_signature,
                    assumptions,
                )
                if snapshot.snapshot_id in rows:
                    raise _InvalidCostAdjustedWalkForwardData(
                        f"duplicate snapshot id: {snapshot.snapshot_id}"
                    )
                rows[snapshot.snapshot_id] = snapshot
            ids = set(rows)
            if expected_ids is None:
                expected_ids = ids
                first_rows = result.snapshots
            elif ids != expected_ids:
                raise _InvalidCostAdjustedWalkForwardData(
                    "scenario cost-adjustable snapshot sets differ"
                )
            indexed[scenario.name] = rows
        ordered = tuple(
            sorted(
                first_rows,
                key=lambda item: (
                    item.captured_at.astimezone(UTC),
                    item.snapshot_id,
                ),
            )
        )
        for snapshot in ordered:
            baseline_identity = self._baseline_identity(snapshot)
            for scenario in scenarios[1:]:
                other = indexed[scenario.name][snapshot.snapshot_id]
                if self._baseline_identity(other) != baseline_identity:
                    raise _InvalidCostAdjustedWalkForwardData(
                        f"cross-scenario baseline mismatch: snapshot={snapshot.snapshot_id}"
                    )
        return ordered, indexed

    @staticmethod
    def _validate_snapshot(
        snapshot: CostAdjustedRankingSnapshotResult,
        cohort: CostAdjustedRankingCohortResult,
        scenario: RankingScenarioDefinition,
        scenario_signature: str | None,
        assumptions: CostAssumptions,
    ) -> None:
        if (
            not isinstance(snapshot.snapshot_id, int)
            or isinstance(snapshot.snapshot_id, bool)
            or snapshot.snapshot_id < 1
            or not isinstance(snapshot.pipeline_run_id, str)
            or not snapshot.pipeline_run_id
            or not isinstance(snapshot.captured_at, datetime)
            or snapshot.captured_at.tzinfo is None
            or snapshot.captured_at.utcoffset() is None
            or snapshot.horizon_minutes != cohort.horizon_minutes
            or snapshot.baseline_policy_signature != cohort.baseline_policy_signature
            or snapshot.effective_top_n != cohort.effective_top_n
            or snapshot.scenario_name != scenario.name
            or snapshot.scenario_definition_signature != scenario.definition_signature
            or not isinstance(scenario_signature, str)
            or not scenario_signature
            or snapshot.scenario_signature != scenario_signature
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                f"invalid snapshot lineage: snapshot={snapshot.snapshot_id}"
            )
        if (
            snapshot.fee_rate != assumptions.fee_rate
            or snapshot.spread_cost_rate != assumptions.spread_cost_rate
            or snapshot.slippage_rate != assumptions.slippage_rate
            or snapshot.total_cost_rate != assumptions.total_cost_rate
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                f"cost assumption mismatch: snapshot={snapshot.snapshot_id}"
            )
        decimals = (
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
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite()
            for value in decimals
        ):
            raise _InvalidCostAdjustedWalkForwardData(
                f"non-finite snapshot metric: snapshot={snapshot.snapshot_id}"
            )

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

    def _build_fold(
        self,
        fold_offset: int,
        ordered: tuple[CostAdjustedRankingSnapshotResult, ...],
        scenario_rows: dict[str, dict[int, CostAdjustedRankingSnapshotResult]],
        scenarios: tuple[RankingScenarioDefinition, ...],
        *,
        initial_research_size: int,
        validation_size: int,
    ) -> CostAdjustedWalkForwardFoldResult:
        research_end = initial_research_size + fold_offset * validation_size
        validation_end = research_end + validation_size
        research_source = ordered[:research_end]
        validation_source = ordered[research_end:validation_end]
        research_ids = tuple(item.snapshot_id for item in research_source)
        validation_ids = tuple(item.snapshot_id for item in validation_source)
        scenario_results = tuple(
            CostAdjustedWalkForwardScenarioFoldResult(
                scenario_name=scenario.name,
                scenario_definition_signature=scenario.definition_signature,
                scenario_signature=scenario_rows[scenario.name][
                    research_ids[0]
                ].scenario_signature,
                research=self._aggregate(
                    tuple(
                        scenario_rows[scenario.name][snapshot_id]
                        for snapshot_id in research_ids
                    )
                ),
                validation=self._aggregate(
                    tuple(
                        scenario_rows[scenario.name][snapshot_id]
                        for snapshot_id in validation_ids
                    )
                ),
            )
            for scenario in scenarios
        )
        return CostAdjustedWalkForwardFoldResult(
            fold_index=fold_offset + 1,
            research_snapshot_count=len(research_ids),
            validation_snapshot_count=len(validation_ids),
            research_start_at=research_source[0].captured_at.astimezone(UTC),
            research_end_at=research_source[-1].captured_at.astimezone(UTC),
            validation_start_at=validation_source[0].captured_at.astimezone(UTC),
            validation_end_at=validation_source[-1].captured_at.astimezone(UTC),
            research_snapshot_ids=research_ids,
            validation_snapshot_ids=validation_ids,
            scenario_results=scenario_results,
        )

    @staticmethod
    def _aggregate(
        snapshots: tuple[CostAdjustedRankingSnapshotResult, ...],
    ) -> CostAdjustedWalkForwardPeriodAggregate:
        gross = tuple(item.gross_return_delta for item in snapshots)
        adjusted = tuple(item.cost_adjusted_return_delta for item in snapshots)
        positive = sum(value > 0 for value in adjusted)
        negative = sum(value < 0 for value in adjusted)
        ties = sum(value == 0 for value in adjusted)
        count = len(snapshots)
        return CostAdjustedWalkForwardPeriodAggregate(
            snapshot_count=count,
            mean_gross_return_delta=sum(gross, Decimal("0")) / Decimal(count),
            median_gross_return_delta=median(gross),
            mean_cost_adjusted_return_delta=(
                sum(adjusted, Decimal("0")) / Decimal(count)
            ),
            median_cost_adjusted_return_delta=median(adjusted),
            positive_cost_adjusted_snapshot_count=positive,
            negative_cost_adjusted_snapshot_count=negative,
            tie_cost_adjusted_snapshot_count=ties,
            positive_cost_adjusted_snapshot_rate=Decimal(positive) / Decimal(count),
        )

    @staticmethod
    def _scenario_summaries(
        scenarios: tuple[RankingScenarioDefinition, ...],
        folds: tuple[CostAdjustedWalkForwardFoldResult, ...],
    ) -> tuple[CostAdjustedWalkForwardScenarioSummary, ...]:
        summaries = []
        for index, scenario in enumerate(scenarios):
            validations = tuple(
                fold.scenario_results[index].validation for fold in folds
            )
            gross = tuple(item.mean_gross_return_delta for item in validations)
            adjusted = tuple(
                item.mean_cost_adjusted_return_delta for item in validations
            )
            validation_snapshots = sum(item.snapshot_count for item in validations)
            positive_snapshots = sum(
                item.positive_cost_adjusted_snapshot_count for item in validations
            )
            negative_snapshots = sum(
                item.negative_cost_adjusted_snapshot_count for item in validations
            )
            tie_snapshots = sum(
                item.tie_cost_adjusted_snapshot_count for item in validations
            )
            summaries.append(
                CostAdjustedWalkForwardScenarioSummary(
                    scenario_name=scenario.name,
                    scenario_definition_signature=scenario.definition_signature,
                    scenario_signature=(
                        folds[0].scenario_results[index].scenario_signature
                        if folds
                        else None
                    ),
                    component_weights=scenario.component_weights,
                    validation_fold_count=len(folds),
                    positive_validation_fold_count=sum(value > 0 for value in adjusted),
                    negative_validation_fold_count=sum(value < 0 for value in adjusted),
                    tie_validation_fold_count=sum(value == 0 for value in adjusted),
                    mean_validation_gross_return_delta=(
                        sum(gross, Decimal("0")) / Decimal(len(gross))
                        if gross
                        else None
                    ),
                    median_validation_gross_return_delta=(
                        median(gross) if gross else None
                    ),
                    mean_validation_cost_adjusted_return_delta=(
                        sum(adjusted, Decimal("0")) / Decimal(len(adjusted))
                        if adjusted
                        else None
                    ),
                    median_validation_cost_adjusted_return_delta=(
                        median(adjusted) if adjusted else None
                    ),
                    validation_snapshot_count=validation_snapshots,
                    positive_validation_snapshot_count=positive_snapshots,
                    negative_validation_snapshot_count=negative_snapshots,
                    tie_validation_snapshot_count=tie_snapshots,
                    positive_validation_snapshot_rate=(
                        Decimal(positive_snapshots) / Decimal(validation_snapshots)
                        if validation_snapshots
                        else None
                    ),
                )
            )
        return tuple(summaries)

    @staticmethod
    def _empty_cohort(
        cohort: CostAdjustedRankingCohortResult,
        scenarios: tuple[RankingScenarioDefinition, ...],
        initial_research_size: int,
        validation_size: int,
        status: str,
        reason: str,
        *,
        unused_tail: int,
    ) -> CostAdjustedWalkForwardCohortResult:
        return CostAdjustedWalkForwardCohortResult(
            horizon_minutes=cohort.horizon_minutes,
            baseline_policy_signature=cohort.baseline_policy_signature,
            effective_top_n=cohort.effective_top_n,
            candidate_ab_snapshot_count=cohort.candidate_ab_snapshot_count,
            common_comparable_ab_snapshot_count=(
                cohort.common_comparable_ab_snapshot_count
            ),
            cost_adjustable_snapshot_count=cohort.cost_adjustable_snapshot_count,
            cost_adjustable_coverage_rate=cohort.cost_adjustable_coverage_rate,
            initial_research_size=initial_research_size,
            validation_size=validation_size,
            step_size=validation_size,
            fold_count=0,
            unused_tail_snapshot_count=unused_tail,
            status=status,
            safe_reason=reason,
            performance_compared=False,
            folds=(),
            scenario_results=tuple(
                CostAdjustedWalkForwardScenarioSummary(
                    scenario_name=scenario.name,
                    scenario_definition_signature=scenario.definition_signature,
                    scenario_signature=None,
                    component_weights=scenario.component_weights,
                    validation_fold_count=0,
                    positive_validation_fold_count=0,
                    negative_validation_fold_count=0,
                    tie_validation_fold_count=0,
                    mean_validation_gross_return_delta=None,
                    median_validation_gross_return_delta=None,
                    mean_validation_cost_adjusted_return_delta=None,
                    median_validation_cost_adjusted_return_delta=None,
                    validation_snapshot_count=0,
                    positive_validation_snapshot_count=0,
                    negative_validation_snapshot_count=0,
                    tie_validation_snapshot_count=0,
                    positive_validation_snapshot_rate=None,
                )
                for scenario in scenarios
            ),
        )

    @staticmethod
    def _overall_status(
        cohorts: tuple[CostAdjustedWalkForwardCohortResult, ...],
    ) -> str:
        if any(
            cohort.status == INVALID_COST_ADJUSTED_WALK_FORWARD_DATA
            for cohort in cohorts
        ):
            return INVALID_COST_ADJUSTED_WALK_FORWARD_DATA
        if any(cohort.status == SUCCESS for cohort in cohorts):
            return SUCCESS
        if any(
            cohort.status == INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA
            for cohort in cohorts
        ):
            return INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA
        return NO_COST_ADJUSTABLE_SNAPSHOTS

    @staticmethod
    def _overall_reason(status: str) -> str:
        return {
            NO_COST_ADJUSTABLE_SNAPSHOTS: ("no cohort has cost-adjustable snapshots"),
            INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA: (
                "no cohort fills one complete cost-adjusted validation fold"
            ),
            INVALID_COST_ADJUSTED_WALK_FORWARD_DATA: (
                "cost-adjusted walk-forward integrity failed"
            ),
        }[status]

    @staticmethod
    def _result(
        source: CostAdjustedRankingEvaluationResult,
        initial_research_size: int,
        validation_size: int,
        cohorts: tuple[CostAdjustedWalkForwardCohortResult, ...],
        status: str,
        reason: str | None,
    ) -> CostAdjustedWalkForwardValidationResult:
        return CostAdjustedWalkForwardValidationResult(
            requested_snapshot_count=source.requested_snapshot_count,
            evaluated_snapshot_count=source.evaluated_snapshot_count,
            scenario_count=source.scenario_count,
            horizon_count=source.horizon_count,
            cohort_count=len(cohorts),
            initial_research_size=initial_research_size,
            validation_size=validation_size,
            step_size=validation_size,
            assumptions=source.assumptions,
            status=status,
            safe_reason=reason,
            policy_decision_performed=False,
            strict_unseen_validation="not_verified",
            scenarios=source.scenarios,
            cohorts=cohorts,
        )


__all__ = [
    "COST_ADJUSTED_METRIC_TYPE",
    "GROSS_PERFORMANCE_METRIC_TYPE",
    "INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA",
    "INVALID_COST_ADJUSTED_WALK_FORWARD_DATA",
    "NO_COST_ADJUSTABLE_SNAPSHOTS",
    "REPORT_TYPE",
    "RESULT_TYPE",
    "SUCCESS",
    "CostAdjustedWalkForwardCohortResult",
    "CostAdjustedWalkForwardFoldResult",
    "CostAdjustedWalkForwardInputError",
    "CostAdjustedWalkForwardPeriodAggregate",
    "CostAdjustedWalkForwardScenarioFoldResult",
    "CostAdjustedWalkForwardScenarioSummary",
    "CostAdjustedWalkForwardValidationResult",
    "CostAdjustedWalkForwardValidationService",
]
