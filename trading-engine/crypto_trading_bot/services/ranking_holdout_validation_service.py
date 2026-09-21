from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    INVALID_SWEEP_DATA,
    NO_COMMON_COMPARABLE_SNAPSHOTS,
    SUCCESS,
    RankingScenarioComparableCohort,
    RankingScenarioDefinition,
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    StrategyABBatchPerformanceResult,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)


RESULT_TYPE = "TEMPORAL_RANKING_HOLDOUT_RESEARCH"
PERFORMANCE_METRIC_TYPE = "RANKING_SELECTION_GROSS_MARKET_PERFORMANCE"
TEMPORAL_RATIO = "TEMPORAL_RATIO"
FIXED_TEMPORAL_CUTOFF = "FIXED_TEMPORAL_CUTOFF"
INSUFFICIENT_TEMPORAL_SPLIT_DATA = "INSUFFICIENT_TEMPORAL_SPLIT_DATA"
INVALID_HOLDOUT_DATA = "INVALID_HOLDOUT_DATA"
DEFAULT_HOLDOUT_RATIO = Decimal("0.30")


class HoldoutInputError(ReplayInputError):
    pass


@dataclass(frozen=True)
class RankingHoldoutPeriodAggregate:
    snapshot_count: int
    scenario_win_count: int
    scenario_loss_count: int
    tie_count: int
    scenario_win_rate: Decimal | None
    mean_baseline_return: Decimal | None
    mean_scenario_return: Decimal | None
    mean_return_delta: Decimal | None
    median_snapshot_return_delta: Decimal | None
    mean_baseline_positive_rate: Decimal | None
    mean_scenario_positive_rate: Decimal | None


@dataclass(frozen=True)
class RankingHoldoutScenarioResult:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    research: RankingHoldoutPeriodAggregate
    holdout: RankingHoldoutPeriodAggregate


@dataclass(frozen=True)
class RankingHoldoutCohortResult:
    horizon_minutes: int
    baseline_policy_signature: str | None
    effective_top_n: int
    candidate_snapshot_count: int
    common_comparable_snapshot_count: int
    common_coverage_rate: Decimal
    split_mode: str
    research_snapshot_count: int
    holdout_snapshot_count: int
    research_start_at: datetime | None
    research_end_at: datetime | None
    holdout_start_at: datetime | None
    holdout_end_at: datetime | None
    status: str
    safe_reason: str | None
    performance_compared: bool
    scenario_results: tuple[RankingHoldoutScenarioResult, ...]


@dataclass(frozen=True)
class RankingHoldoutValidationResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenario_count: int
    horizon_count: int
    cohort_count: int
    split_mode: str
    holdout_ratio: Decimal | None
    research_cutoff_at: datetime | None
    strict_unseen_holdout: str
    policy_decision_performed: bool
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[RankingHoldoutCohortResult, ...]


def ordered_common_results(
    cohort: RankingScenarioComparableCohort,
    scenarios: tuple[RankingScenarioDefinition, ...],
) -> tuple[tuple[StrategyABSnapshotPerformanceResult, ...], str | None]:
    """Return the shared common set in canonical temporal order."""
    first = {
        result.snapshot_id: result for result in cohort.results_for(scenarios[0].name)
    }
    rows: list[StrategyABSnapshotPerformanceResult] = []
    for snapshot_id in cohort.common_snapshot_ids:
        result = first.get(snapshot_id)
        if (
            result is None
            or not isinstance(result.snapshot_id, int)
            or isinstance(result.snapshot_id, bool)
            or not isinstance(result.captured_at, datetime)
            or result.captured_at.tzinfo is None
            or result.captured_at.utcoffset() is None
        ):
            return (), f"invalid temporal metadata: snapshot={snapshot_id}"
        rows.append(result)
    return (
        tuple(
            sorted(
                rows,
                key=lambda result: (
                    result.captured_at.astimezone(UTC),
                    result.snapshot_id,
                ),
            )
        ),
        None,
    )


def summarize_period_results(
    performance_service: StrategyABPerformanceService,
    results: tuple[StrategyABSnapshotPerformanceResult, ...],
) -> RankingHoldoutPeriodAggregate:
    """Map the existing A/B summary semantics into the shared period model."""
    summary: StrategyABBatchPerformanceResult = performance_service.summarize_results(
        len(results), results
    )
    return RankingHoldoutPeriodAggregate(
        snapshot_count=summary.successful_snapshot_count,
        scenario_win_count=summary.scenario_win_count,
        scenario_loss_count=summary.scenario_loss_count,
        tie_count=summary.tie_count,
        scenario_win_rate=summary.scenario_win_rate,
        mean_baseline_return=summary.mean_baseline_return,
        mean_scenario_return=summary.mean_scenario_return,
        mean_return_delta=summary.mean_return_delta,
        median_snapshot_return_delta=summary.median_snapshot_return_delta,
        mean_baseline_positive_rate=summary.mean_baseline_positive_rate,
        mean_scenario_positive_rate=summary.mean_scenario_positive_rate,
    )


class RankingHoldoutValidationService:
    """Temporally split shared comparable scenario results without DB writes."""

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
        latest: int | None = None,
        holdout_ratio: Decimal | str | None = None,
        research_cutoff_at: datetime | None = None,
    ) -> RankingHoldoutValidationResult:
        if holdout_ratio is not None and research_cutoff_at is not None:
            raise HoldoutInputError(
                "holdout-ratio and research-cutoff-at are mutually exclusive"
            )
        if research_cutoff_at is None:
            split_mode = TEMPORAL_RATIO
            ratio = self._validate_ratio(
                DEFAULT_HOLDOUT_RATIO if holdout_ratio is None else holdout_ratio
            )
            cutoff = None
            strict_unseen = "false"
        else:
            split_mode = FIXED_TEMPORAL_CUTOFF
            ratio = None
            cutoff = self._validate_cutoff(research_cutoff_at)
            strict_unseen = "not_verified"

        matrix = self.sweep_service.evaluate_matrix(
            scenarios=scenarios,
            horizons=horizons,
            latest=latest,
        )
        cohorts = tuple(
            self._evaluate_cohort(
                cohort,
                matrix.scenarios,
                split_mode=split_mode,
                ratio=ratio,
                cutoff=cutoff,
            )
            for cohort in matrix.cohorts
        )
        return RankingHoldoutValidationResult(
            requested_snapshot_count=matrix.requested_snapshot_count,
            evaluated_snapshot_count=matrix.evaluated_snapshot_count,
            scenario_count=len(matrix.scenarios),
            horizon_count=len(matrix.horizons),
            cohort_count=len(cohorts),
            split_mode=split_mode,
            holdout_ratio=ratio,
            research_cutoff_at=cutoff,
            strict_unseen_holdout=strict_unseen,
            policy_decision_performed=False,
            scenarios=matrix.scenarios,
            cohorts=cohorts,
        )

    @staticmethod
    def _validate_ratio(value: Decimal | str) -> Decimal:
        if isinstance(value, bool):
            raise HoldoutInputError(
                "holdout ratio must be greater than 0 and less than 1"
            )
        try:
            ratio = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise HoldoutInputError(
                "holdout ratio must be greater than 0 and less than 1"
            ) from error
        if not ratio.is_finite() or ratio <= 0 or ratio >= 1:
            raise HoldoutInputError(
                "holdout ratio must be greater than 0 and less than 1"
            )
        return ratio

    @staticmethod
    def _validate_cutoff(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise HoldoutInputError("research cutoff must be timezone-aware")
        try:
            offset = value.utcoffset()
        except (OverflowError, ValueError) as error:
            raise HoldoutInputError("research cutoff must be timezone-aware") from error
        if offset is None:
            raise HoldoutInputError("research cutoff must be timezone-aware")
        return value.astimezone(UTC)

    def _evaluate_cohort(
        self,
        cohort: RankingScenarioComparableCohort,
        scenarios: tuple[RankingScenarioDefinition, ...],
        *,
        split_mode: str,
        ratio: Decimal | None,
        cutoff: datetime | None,
    ) -> RankingHoldoutCohortResult:
        status = cohort.status
        safe_reason = cohort.safe_reason
        research_ids: tuple[int | None, ...] = ()
        holdout_ids: tuple[int | None, ...] = ()
        ordered, chronology_error = ordered_common_results(cohort, scenarios)
        if status == INVALID_SWEEP_DATA:
            status = INVALID_HOLDOUT_DATA
        elif chronology_error is not None:
            status = INVALID_HOLDOUT_DATA
            safe_reason = chronology_error
        elif status == NO_COMMON_COMPARABLE_SNAPSHOTS:
            pass
        elif len(ordered) < 2:
            status = INSUFFICIENT_TEMPORAL_SPLIT_DATA
            safe_reason = "at least two common comparable snapshots are required"
        elif split_mode == TEMPORAL_RATIO:
            if ratio is None:
                raise RuntimeError("temporal ratio split lacks a ratio")
            holdout_count = int(
                (Decimal(len(ordered)) * ratio).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            holdout_count = min(max(holdout_count, 1), len(ordered) - 1)
            research_ids = tuple(
                result.snapshot_id for result in ordered[:-holdout_count]
            )
            holdout_ids = tuple(
                result.snapshot_id for result in ordered[-holdout_count:]
            )
            status = SUCCESS
            safe_reason = None
        else:
            if cutoff is None:
                raise RuntimeError("fixed temporal split lacks a cutoff")
            research_ids = tuple(
                result.snapshot_id
                for result in ordered
                if result.captured_at.astimezone(UTC) <= cutoff
            )
            holdout_ids = tuple(
                result.snapshot_id
                for result in ordered
                if result.captured_at.astimezone(UTC) > cutoff
            )
            if not research_ids or not holdout_ids:
                status = INSUFFICIENT_TEMPORAL_SPLIT_DATA
                safe_reason = "fixed cutoff must leave snapshots on both sides"
                research_ids = ()
                holdout_ids = ()
            else:
                status = SUCCESS
                safe_reason = None

        scenario_results = tuple(
            self._scenario_result(
                scenario,
                cohort.results_for(scenario.name),
                research_ids,
                holdout_ids,
            )
            for scenario in scenarios
        )
        first_results = cohort.results_for(scenarios[0].name)
        by_id = {result.snapshot_id: result for result in first_results}
        research_times = tuple(
            by_id[snapshot_id].captured_at.astimezone(UTC)
            for snapshot_id in research_ids
        )
        holdout_times = tuple(
            by_id[snapshot_id].captured_at.astimezone(UTC)
            for snapshot_id in holdout_ids
        )
        return RankingHoldoutCohortResult(
            horizon_minutes=cohort.horizon_minutes,
            baseline_policy_signature=cohort.baseline_policy_signature,
            effective_top_n=cohort.effective_top_n,
            candidate_snapshot_count=len(cohort.candidate_snapshot_ids),
            common_comparable_snapshot_count=len(cohort.common_snapshot_ids),
            common_coverage_rate=cohort.common_coverage_rate,
            split_mode=split_mode,
            research_snapshot_count=len(research_ids),
            holdout_snapshot_count=len(holdout_ids),
            research_start_at=research_times[0] if research_times else None,
            research_end_at=research_times[-1] if research_times else None,
            holdout_start_at=holdout_times[0] if holdout_times else None,
            holdout_end_at=holdout_times[-1] if holdout_times else None,
            status=status,
            safe_reason=safe_reason,
            performance_compared=status == SUCCESS,
            scenario_results=scenario_results,
        )

    def _scenario_result(
        self,
        scenario: RankingScenarioDefinition,
        results: tuple[StrategyABSnapshotPerformanceResult, ...],
        research_ids: tuple[int | None, ...],
        holdout_ids: tuple[int | None, ...],
    ) -> RankingHoldoutScenarioResult:
        indexed = {result.snapshot_id: result for result in results}
        research = tuple(indexed[snapshot_id] for snapshot_id in research_ids)
        holdout = tuple(indexed[snapshot_id] for snapshot_id in holdout_ids)
        return RankingHoldoutScenarioResult(
            scenario_name=scenario.name,
            scenario_definition_signature=scenario.definition_signature,
            component_weights=scenario.component_weights,
            research=summarize_period_results(self.performance_service, research),
            holdout=summarize_period_results(self.performance_service, holdout),
        )
