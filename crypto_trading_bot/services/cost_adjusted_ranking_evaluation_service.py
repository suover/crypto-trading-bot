from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    INVALID_SWEEP_DATA,
    SUCCESS as SWEEP_SUCCESS,
    RankingScenarioComparableCohort,
    RankingScenarioDefinition,
    RankingScenarioEvaluationMatrix,
    RankingScenarioSweepService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    RESULT_TYPE as AB_GROSS_PERFORMANCE_METRIC_TYPE,
    SUCCESS as AB_SUCCESS,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    INVALID_TURNOVER_DATA,
    SUCCESS as TURNOVER_SUCCESS,
    RankingSelectionTransition,
    TemporalRankingTurnoverCohortResult,
    TemporalRankingTurnoverResult,
    TemporalRankingTurnoverScenarioTransition,
    TemporalRankingTurnoverService,
    TemporalRankingTurnoverTransition,
)


RESULT_TYPE = "COST_ADJUSTED_RANKING_COUNTERFACTUAL_RESEARCH"
COST_ADJUSTED_METRIC_TYPE = "COST_ADJUSTED_RANKING_SELECTION_PERFORMANCE_PROXY"
GROSS_PERFORMANCE_METRIC_TYPE = AB_GROSS_PERFORMANCE_METRIC_TYPE
SUCCESS = "SUCCESS"
NO_TURNOVER_TRANSITIONS = "NO_TURNOVER_TRANSITIONS"
NO_COMMON_COMPARABLE_SNAPSHOTS = "NO_COMMON_COMPARABLE_SNAPSHOTS"
NO_COST_ADJUSTABLE_SNAPSHOTS = "NO_COST_ADJUSTABLE_SNAPSHOTS"
INVALID_COST_ADJUSTED_DATA = "INVALID_COST_ADJUSTED_DATA"


@dataclass(frozen=True)
class CostAssumptions:
    fee_rate: Decimal
    spread_cost_rate: Decimal
    slippage_rate: Decimal
    total_cost_rate: Decimal


@dataclass(frozen=True)
class SelectionChangeCost:
    target_weight: Decimal
    replacement_rate: Decimal
    sell_notional_ratio: Decimal
    buy_notional_ratio: Decimal
    gross_traded_notional_ratio: Decimal
    execution_cost_ratio: Decimal
    execution_cost_percentage: Decimal


@dataclass(frozen=True)
class CostAdjustedRankingSnapshotResult:
    snapshot_id: int
    pipeline_run_id: str
    captured_at: datetime
    horizon_minutes: int
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
    baseline_sell_notional_ratio: Decimal
    baseline_buy_notional_ratio: Decimal
    baseline_gross_traded_notional_ratio: Decimal
    baseline_execution_cost_ratio: Decimal
    baseline_execution_cost_percentage: Decimal
    scenario_replacement_rate: Decimal
    scenario_sell_notional_ratio: Decimal
    scenario_buy_notional_ratio: Decimal
    scenario_gross_traded_notional_ratio: Decimal
    scenario_execution_cost_ratio: Decimal
    scenario_execution_cost_percentage: Decimal
    baseline_gross_return: Decimal
    scenario_gross_return: Decimal
    gross_return_delta: Decimal
    baseline_cost_adjusted_return: Decimal
    scenario_cost_adjusted_return: Decimal
    cost_adjusted_return_delta: Decimal


@dataclass(frozen=True)
class CostAdjustedRankingScenarioResult:
    scenario_name: str
    scenario_definition_signature: str
    scenario_signature: str | None
    cost_adjusted_snapshot_count: int
    mean_baseline_gross_return: Decimal | None
    mean_scenario_gross_return: Decimal | None
    mean_gross_return_delta: Decimal | None
    mean_baseline_gross_traded_notional_ratio: Decimal | None
    mean_scenario_gross_traded_notional_ratio: Decimal | None
    mean_baseline_execution_cost_percentage: Decimal | None
    mean_scenario_execution_cost_percentage: Decimal | None
    mean_execution_cost_delta_percentage: Decimal | None
    mean_baseline_cost_adjusted_return: Decimal | None
    mean_scenario_cost_adjusted_return: Decimal | None
    mean_cost_adjusted_return_delta: Decimal | None
    median_cost_adjusted_return_delta: Decimal | None
    cost_adjusted_scenario_win_count: int
    cost_adjusted_scenario_loss_count: int
    cost_adjusted_tie_count: int
    cost_adjusted_scenario_win_rate: Decimal | None
    snapshots: tuple[CostAdjustedRankingSnapshotResult, ...]


@dataclass(frozen=True)
class CostAdjustedRankingCohortResult:
    horizon_minutes: int
    baseline_policy_signature: str | None
    effective_top_n: int
    candidate_ab_snapshot_count: int
    common_comparable_ab_snapshot_count: int
    turnover_transition_count: int
    cost_adjustable_snapshot_count: int
    cost_adjustable_coverage_rate: Decimal
    status: str
    safe_reason: str | None
    performance_compared: bool
    scenario_results: tuple[CostAdjustedRankingScenarioResult, ...]


@dataclass(frozen=True)
class CostAdjustedRankingEvaluationResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenario_count: int
    horizon_count: int
    cohort_count: int
    status: str
    safe_reason: str | None
    assumptions: CostAssumptions
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[CostAdjustedRankingCohortResult, ...]


class _InvalidCostAdjustedData(Exception):
    pass


def parse_cost_rate(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ReplayInputError(f"{field_name} must be a Decimal fraction")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ReplayInputError(f"{field_name} must be a Decimal fraction") from error
    if not parsed.is_finite() or parsed < 0 or parsed >= 1:
        raise ReplayInputError(f"{field_name} must be finite and satisfy 0 <= rate < 1")
    return parsed


def build_cost_assumptions(
    *, fee_rate: object, spread_cost_rate: object, slippage_rate: object
) -> CostAssumptions:
    fee = parse_cost_rate(fee_rate, field_name="fee_rate")
    spread = parse_cost_rate(spread_cost_rate, field_name="spread_cost_rate")
    slippage = parse_cost_rate(slippage_rate, field_name="slippage_rate")
    return CostAssumptions(
        fee_rate=fee,
        spread_cost_rate=spread,
        slippage_rate=slippage,
        total_cost_rate=fee + spread + slippage,
    )


def _decimal(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise _InvalidCostAdjustedData(f"{field_name} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise _InvalidCostAdjustedData(f"{field_name} is invalid") from error
    if not parsed.is_finite():
        raise _InvalidCostAdjustedData(f"{field_name} is not finite")
    return parsed


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    return sum(values, Decimal("0")) / len(values) if values else None


class CostAdjustedRankingEvaluationService:
    """Align turnover and stored gross outcomes without writes or external calls."""

    def __init__(
        self,
        session: Session,
        *,
        turnover_service: TemporalRankingTurnoverService | None = None,
        sweep_service: RankingScenarioSweepService | None = None,
        performance_service: StrategyABPerformanceService | None = None,
    ) -> None:
        self.turnover_service = turnover_service or TemporalRankingTurnoverService(
            session
        )
        if sweep_service is None:
            resolved_performance = performance_service or StrategyABPerformanceService(
                session
            )
            sweep_service = RankingScenarioSweepService(
                session, performance_service=resolved_performance
            )
        else:
            resolved_performance = (
                performance_service or sweep_service.performance_service
            )
        self.sweep_service = sweep_service
        self.performance_service = resolved_performance

    def evaluate(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        latest: int,
        fee_rate: object,
        spread_cost_rate: object,
        slippage_rate: object,
    ) -> CostAdjustedRankingEvaluationResult:
        definitions = RankingScenarioSweepService._validate_definitions(
            tuple(scenarios)
        )
        normalized_horizons = RankingScenarioSweepService._validate_horizons(horizons)
        if isinstance(latest, bool) or not isinstance(latest, int) or latest < 1:
            raise ReplayInputError("latest snapshot count must be >= 1")
        assumptions = build_cost_assumptions(
            fee_rate=fee_rate,
            spread_cost_rate=spread_cost_rate,
            slippage_rate=slippage_rate,
        )
        turnover = self.turnover_service.evaluate(
            scenarios=definitions,
            latest=latest,
        )
        if turnover.status == INVALID_TURNOVER_DATA:
            return self._empty_result(
                turnover,
                assumptions,
                normalized_horizons,
                INVALID_COST_ADJUSTED_DATA,
                f"turnover integrity failed: {turnover.safe_reason}",
            )
        if turnover.status != TURNOVER_SUCCESS:
            return self._empty_result(
                turnover,
                assumptions,
                normalized_horizons,
                NO_TURNOVER_TRANSITIONS,
                f"turnover has no valid transition: {turnover.status}",
            )

        try:
            matrix = self.sweep_service.evaluate_matrix(
                scenarios=definitions,
                horizons=normalized_horizons,
                latest=latest,
            )
        except ReplayInputError as error:
            return self._empty_result(
                turnover,
                assumptions,
                normalized_horizons,
                INVALID_COST_ADJUSTED_DATA,
                f"A/B scenario matrix integrity failed: {error}",
            )
        try:
            return self._evaluate_aligned(turnover, matrix, assumptions)
        except _InvalidCostAdjustedData as error:
            return self._empty_result(
                turnover,
                assumptions,
                matrix.horizons,
                INVALID_COST_ADJUSTED_DATA,
                str(error),
            )

    def _evaluate_aligned(
        self,
        turnover: TemporalRankingTurnoverResult,
        matrix: RankingScenarioEvaluationMatrix,
        assumptions: CostAssumptions,
    ) -> CostAdjustedRankingEvaluationResult:
        if matrix.requested_snapshot_count != turnover.requested_snapshot_count:
            raise _InvalidCostAdjustedData("requested snapshot counts do not match")
        if matrix.scenarios != turnover.scenarios:
            raise _InvalidCostAdjustedData("scenario definitions do not match")
        if any(cohort.status == INVALID_SWEEP_DATA for cohort in matrix.cohorts):
            raise _InvalidCostAdjustedData("A/B scenario matrix integrity failed")

        turnover_cohorts: dict[
            tuple[str, int], TemporalRankingTurnoverCohortResult
        ] = {}
        for cohort in turnover.cohorts:
            key = (cohort.baseline_policy_signature, cohort.effective_top_n)
            if key in turnover_cohorts:
                raise _InvalidCostAdjustedData("duplicate turnover cohort")
            turnover_cohorts[key] = cohort

        matrix_keys: set[tuple[int, str | None, int]] = set()
        cohorts: list[CostAdjustedRankingCohortResult] = []
        for ab_cohort in matrix.cohorts:
            key = (
                ab_cohort.horizon_minutes,
                ab_cohort.baseline_policy_signature,
                ab_cohort.effective_top_n,
            )
            if key in matrix_keys:
                raise _InvalidCostAdjustedData("duplicate A/B cohort")
            matrix_keys.add(key)
            turnover_cohort = (
                turnover_cohorts.get(
                    (
                        ab_cohort.baseline_policy_signature,
                        ab_cohort.effective_top_n,
                    )
                )
                if ab_cohort.baseline_policy_signature is not None
                else None
            )
            cohorts.append(
                self._build_cohort(
                    turnover_cohort,
                    ab_cohort,
                    matrix.scenarios,
                    assumptions,
                )
            )

        status = self._overall_status(tuple(cohorts))
        return CostAdjustedRankingEvaluationResult(
            requested_snapshot_count=turnover.requested_snapshot_count,
            evaluated_snapshot_count=matrix.evaluated_snapshot_count,
            scenario_count=len(matrix.scenarios),
            horizon_count=len(matrix.horizons),
            cohort_count=len(cohorts),
            status=status,
            safe_reason=None if status == SUCCESS else self._overall_reason(status),
            assumptions=assumptions,
            scenarios=matrix.scenarios,
            cohorts=tuple(cohorts),
        )

    def _build_cohort(
        self,
        turnover_cohort: TemporalRankingTurnoverCohortResult | None,
        ab_cohort: RankingScenarioComparableCohort,
        definitions: tuple[RankingScenarioDefinition, ...],
        assumptions: CostAssumptions,
    ) -> CostAdjustedRankingCohortResult:
        candidate_count = len(ab_cohort.candidate_snapshot_ids)
        common_count = len(ab_cohort.common_snapshot_ids)
        if ab_cohort.status != SWEEP_SUCCESS:
            return self._empty_cohort(
                ab_cohort,
                definitions,
                assumptions,
                NO_COMMON_COMPARABLE_SNAPSHOTS,
                ab_cohort.safe_reason or "no common comparable A/B snapshots",
                0 if turnover_cohort is None else turnover_cohort.transition_count,
            )
        if turnover_cohort is None or not turnover_cohort.transitions:
            return self._empty_cohort(
                ab_cohort,
                definitions,
                assumptions,
                NO_TURNOVER_TRANSITIONS,
                "no matching turnover transition cohort",
                0,
            )

        self._validate_turnover_scenario_identity(turnover_cohort, definitions)

        transitions = self._index_transitions(turnover_cohort)
        common_ids = self._validated_common_ids(ab_cohort.common_snapshot_ids)
        adjustable_ids = tuple(
            transition.baseline.current_snapshot_id
            for transition in turnover_cohort.transitions
            if transition.baseline.current_snapshot_id in common_ids
        )
        if not adjustable_ids:
            return self._empty_cohort(
                ab_cohort,
                definitions,
                assumptions,
                NO_COST_ADJUSTABLE_SNAPSHOTS,
                "valid turnover current snapshots and common A/B snapshots do not overlap",
                len(transitions),
            )

        scenario_results = tuple(
            self._build_scenario_result(
                definition,
                ab_cohort,
                transitions,
                adjustable_ids,
                assumptions,
            )
            for definition in definitions
        )
        expected_counts = {
            result.cost_adjusted_snapshot_count for result in scenario_results
        }
        if expected_counts != {len(adjustable_ids)}:
            raise _InvalidCostAdjustedData("scenario cost-adjusted subsets differ")
        return CostAdjustedRankingCohortResult(
            horizon_minutes=ab_cohort.horizon_minutes,
            baseline_policy_signature=ab_cohort.baseline_policy_signature,
            effective_top_n=ab_cohort.effective_top_n,
            candidate_ab_snapshot_count=candidate_count,
            common_comparable_ab_snapshot_count=common_count,
            turnover_transition_count=len(transitions),
            cost_adjustable_snapshot_count=len(adjustable_ids),
            cost_adjustable_coverage_rate=(
                Decimal(len(adjustable_ids)) / Decimal(common_count)
            ),
            status=SUCCESS,
            safe_reason=None,
            performance_compared=True,
            scenario_results=scenario_results,
        )

    def _build_scenario_result(
        self,
        definition: RankingScenarioDefinition,
        ab_cohort: RankingScenarioComparableCohort,
        transitions: dict[int, TemporalRankingTurnoverTransition],
        adjustable_ids: tuple[int, ...],
        assumptions: CostAssumptions,
    ) -> CostAdjustedRankingScenarioResult:
        ab_rows = self._index_ab_results(ab_cohort.results_for(definition.name))
        snapshots: list[CostAdjustedRankingSnapshotResult] = []
        subset: list[StrategyABSnapshotPerformanceResult] = []
        for snapshot_id in adjustable_ids:
            row = ab_rows.get(snapshot_id)
            if row is None or row.status != AB_SUCCESS:
                raise _InvalidCostAdjustedData(
                    f"common A/B result is missing or not SUCCESS: snapshot_id={snapshot_id}"
                )
            if row.horizon_minutes != ab_cohort.horizon_minutes:
                raise _InvalidCostAdjustedData(
                    f"A/B horizon mismatch: snapshot_id={snapshot_id}"
                )
            if (
                row.baseline_policy_signature != ab_cohort.baseline_policy_signature
                or row.effective_top_n != ab_cohort.effective_top_n
            ):
                raise _InvalidCostAdjustedData(
                    f"A/B cohort lineage mismatch: snapshot_id={snapshot_id}"
                )
            transition = transitions[snapshot_id]
            scenario_transition = self._scenario_transition(transition, definition.name)
            snapshots.append(
                self._snapshot_result(
                    definition,
                    transition.baseline,
                    scenario_transition,
                    row,
                    assumptions,
                )
            )
            subset.append(row)

        gross = self.performance_service.summarize_results(len(subset), tuple(subset))
        if gross.successful_snapshot_count != len(subset):
            raise _InvalidCostAdjustedData("gross summary did not preserve the subset")
        values = tuple(snapshots)
        deltas = tuple(item.cost_adjusted_return_delta for item in values)
        scenario_signatures = {item.scenario_signature for item in values}
        if len(scenario_signatures) != 1:
            raise _InvalidCostAdjustedData("scenario signature changed within cohort")
        wins = sum(value > 0 for value in deltas)
        losses = sum(value < 0 for value in deltas)
        ties = sum(value == 0 for value in deltas)
        return CostAdjustedRankingScenarioResult(
            scenario_name=definition.name,
            scenario_definition_signature=definition.definition_signature,
            scenario_signature=scenario_signatures.pop(),
            cost_adjusted_snapshot_count=len(values),
            mean_baseline_gross_return=gross.mean_baseline_return,
            mean_scenario_gross_return=gross.mean_scenario_return,
            mean_gross_return_delta=gross.mean_return_delta,
            mean_baseline_gross_traded_notional_ratio=_mean(
                tuple(item.baseline_gross_traded_notional_ratio for item in values)
            ),
            mean_scenario_gross_traded_notional_ratio=_mean(
                tuple(item.scenario_gross_traded_notional_ratio for item in values)
            ),
            mean_baseline_execution_cost_percentage=_mean(
                tuple(item.baseline_execution_cost_percentage for item in values)
            ),
            mean_scenario_execution_cost_percentage=_mean(
                tuple(item.scenario_execution_cost_percentage for item in values)
            ),
            mean_execution_cost_delta_percentage=_mean(
                tuple(
                    item.scenario_execution_cost_percentage
                    - item.baseline_execution_cost_percentage
                    for item in values
                )
            ),
            mean_baseline_cost_adjusted_return=_mean(
                tuple(item.baseline_cost_adjusted_return for item in values)
            ),
            mean_scenario_cost_adjusted_return=_mean(
                tuple(item.scenario_cost_adjusted_return for item in values)
            ),
            mean_cost_adjusted_return_delta=_mean(deltas),
            median_cost_adjusted_return_delta=median(deltas),
            cost_adjusted_scenario_win_count=wins,
            cost_adjusted_scenario_loss_count=losses,
            cost_adjusted_tie_count=ties,
            cost_adjusted_scenario_win_rate=Decimal(wins) / Decimal(len(values)),
            snapshots=values,
        )

    def _snapshot_result(
        self,
        definition: RankingScenarioDefinition,
        baseline_transition: RankingSelectionTransition,
        scenario_transition: TemporalRankingTurnoverScenarioTransition,
        ab: StrategyABSnapshotPerformanceResult,
        assumptions: CostAssumptions,
    ) -> CostAdjustedRankingSnapshotResult:
        self._validate_lineage(
            baseline_transition,
            scenario_transition,
            ab,
            definition,
        )
        baseline_cost = self._selection_cost(baseline_transition, assumptions)
        scenario_cost = self._selection_cost(
            scenario_transition.transition, assumptions
        )
        baseline_return = _decimal(
            ab.baseline_mean_return, field_name="baseline_mean_return"
        )
        scenario_return = _decimal(
            ab.scenario_mean_return, field_name="scenario_mean_return"
        )
        gross_delta = _decimal(ab.mean_return_delta, field_name="mean_return_delta")
        if scenario_return - baseline_return != gross_delta:
            raise _InvalidCostAdjustedData("gross return delta identity is invalid")
        baseline_adjusted = baseline_return - baseline_cost.execution_cost_percentage
        scenario_adjusted = scenario_return - scenario_cost.execution_cost_percentage
        adjusted_delta = scenario_adjusted - baseline_adjusted
        if adjusted_delta != gross_delta - (
            scenario_cost.execution_cost_percentage
            - baseline_cost.execution_cost_percentage
        ):
            raise _InvalidCostAdjustedData("cost-adjusted return identity is invalid")
        return CostAdjustedRankingSnapshotResult(
            snapshot_id=ab.snapshot_id,
            pipeline_run_id=ab.pipeline_run_id,
            captured_at=ab.captured_at.astimezone(UTC),
            horizon_minutes=ab.horizon_minutes,
            baseline_policy_signature=ab.baseline_policy_signature,
            effective_top_n=ab.effective_top_n,
            scenario_name=definition.name,
            scenario_definition_signature=definition.definition_signature,
            scenario_signature=ab.scenario_signature,
            fee_rate=assumptions.fee_rate,
            spread_cost_rate=assumptions.spread_cost_rate,
            slippage_rate=assumptions.slippage_rate,
            total_cost_rate=assumptions.total_cost_rate,
            target_weight=baseline_cost.target_weight,
            baseline_replacement_rate=baseline_cost.replacement_rate,
            baseline_sell_notional_ratio=baseline_cost.sell_notional_ratio,
            baseline_buy_notional_ratio=baseline_cost.buy_notional_ratio,
            baseline_gross_traded_notional_ratio=(
                baseline_cost.gross_traded_notional_ratio
            ),
            baseline_execution_cost_ratio=baseline_cost.execution_cost_ratio,
            baseline_execution_cost_percentage=(
                baseline_cost.execution_cost_percentage
            ),
            scenario_replacement_rate=scenario_cost.replacement_rate,
            scenario_sell_notional_ratio=scenario_cost.sell_notional_ratio,
            scenario_buy_notional_ratio=scenario_cost.buy_notional_ratio,
            scenario_gross_traded_notional_ratio=(
                scenario_cost.gross_traded_notional_ratio
            ),
            scenario_execution_cost_ratio=scenario_cost.execution_cost_ratio,
            scenario_execution_cost_percentage=(
                scenario_cost.execution_cost_percentage
            ),
            baseline_gross_return=baseline_return,
            scenario_gross_return=scenario_return,
            gross_return_delta=gross_delta,
            baseline_cost_adjusted_return=baseline_adjusted,
            scenario_cost_adjusted_return=scenario_adjusted,
            cost_adjusted_return_delta=adjusted_delta,
        )

    @staticmethod
    def _selection_cost(
        transition: RankingSelectionTransition,
        assumptions: CostAssumptions,
    ) -> SelectionChangeCost:
        top_n = transition.effective_top_n
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
            raise _InvalidCostAdjustedData("effective TopN is invalid")
        if (
            transition.entered_count != len(transition.entered_markets)
            or transition.exited_count != len(transition.exited_markets)
            or transition.entered_count != transition.exited_count
        ):
            raise _InvalidCostAdjustedData("turnover transition counts are invalid")
        target_weight = Decimal("1") / Decimal(top_n)
        replacement = _decimal(
            transition.replacement_rate, field_name="replacement_rate"
        )
        expected_replacement = Decimal(transition.entered_count) / Decimal(top_n)
        if replacement < 0 or replacement > 1 or replacement != expected_replacement:
            raise _InvalidCostAdjustedData("turnover notional identity is invalid")
        sell = replacement
        buy = replacement
        gross = Decimal("2") * replacement
        cost_ratio = gross * assumptions.total_cost_rate
        cost_percentage = cost_ratio * Decimal("100")
        return SelectionChangeCost(
            target_weight=target_weight,
            replacement_rate=replacement,
            sell_notional_ratio=sell,
            buy_notional_ratio=buy,
            gross_traded_notional_ratio=gross,
            execution_cost_ratio=cost_ratio,
            execution_cost_percentage=cost_percentage,
        )

    @staticmethod
    def _validate_lineage(
        baseline: RankingSelectionTransition,
        scenario: TemporalRankingTurnoverScenarioTransition,
        ab: StrategyABSnapshotPerformanceResult,
        definition: RankingScenarioDefinition,
    ) -> None:
        if (
            ab.status != AB_SUCCESS
            or ab.performance_evaluated is not True
            or not isinstance(ab.snapshot_id, int)
            or isinstance(ab.snapshot_id, bool)
            or ab.snapshot_id != baseline.current_snapshot_id
            or not isinstance(ab.pipeline_run_id, str)
            or not ab.pipeline_run_id
            or not isinstance(ab.captured_at, datetime)
            or ab.captured_at.tzinfo is None
            or ab.captured_at.astimezone(UTC) != baseline.current_captured_at
            or ab.baseline_policy_signature is None
            or ab.effective_top_n != baseline.effective_top_n
            or ab.baseline_top_markets != baseline.current_top_markets
            or ab.scenario_top_markets != scenario.transition.current_top_markets
            or scenario.scenario_name != definition.name
            or ab.scenario_signature != scenario.scenario_signature
            or scenario.transition.previous_snapshot_id != baseline.previous_snapshot_id
            or scenario.transition.current_snapshot_id != baseline.current_snapshot_id
            or scenario.transition.previous_captured_at != baseline.previous_captured_at
            or scenario.transition.current_captured_at != baseline.current_captured_at
            or scenario.transition.effective_top_n != baseline.effective_top_n
        ):
            raise _InvalidCostAdjustedData(
                f"turnover/A-B lineage mismatch: snapshot_id={ab.snapshot_id}"
            )

    @staticmethod
    def _scenario_transition(
        transition: TemporalRankingTurnoverTransition, scenario_name: str
    ) -> TemporalRankingTurnoverScenarioTransition:
        matches = tuple(
            item for item in transition.scenarios if item.scenario_name == scenario_name
        )
        if len(matches) != 1:
            raise _InvalidCostAdjustedData(
                f"turnover scenario transition missing or duplicated: {scenario_name}"
            )
        return matches[0]

    @staticmethod
    def _validate_turnover_scenario_identity(
        cohort: TemporalRankingTurnoverCohortResult,
        definitions: tuple[RankingScenarioDefinition, ...],
    ) -> None:
        summaries = {
            summary.scenario_name: summary for summary in cohort.scenario_summaries
        }
        if len(summaries) != len(cohort.scenario_summaries):
            raise _InvalidCostAdjustedData("duplicate turnover scenario summary")
        for definition in definitions:
            summary = summaries.get(definition.name)
            if (
                summary is None
                or summary.scenario_definition_signature
                != definition.definition_signature
                or not summary.scenario_signature
            ):
                raise _InvalidCostAdjustedData("turnover scenario identity mismatch")
            for transition in cohort.transitions:
                scenario = CostAdjustedRankingEvaluationService._scenario_transition(
                    transition, definition.name
                )
                if scenario.scenario_signature != summary.scenario_signature:
                    raise _InvalidCostAdjustedData(
                        "turnover scenario signature changed within cohort"
                    )

    @staticmethod
    def _index_transitions(
        cohort: TemporalRankingTurnoverCohortResult,
    ) -> dict[int, TemporalRankingTurnoverTransition]:
        indexed: dict[int, TemporalRankingTurnoverTransition] = {}
        for transition in cohort.transitions:
            snapshot_id = transition.baseline.current_snapshot_id
            if snapshot_id in indexed:
                raise _InvalidCostAdjustedData("duplicate turnover current snapshot")
            indexed[snapshot_id] = transition
        if len(indexed) != cohort.transition_count:
            raise _InvalidCostAdjustedData("turnover transition count mismatch")
        return indexed

    @staticmethod
    def _index_ab_results(
        results: tuple[StrategyABSnapshotPerformanceResult, ...],
    ) -> dict[int, StrategyABSnapshotPerformanceResult]:
        indexed: dict[int, StrategyABSnapshotPerformanceResult] = {}
        for result in results:
            if not isinstance(result.snapshot_id, int) or isinstance(
                result.snapshot_id, bool
            ):
                raise _InvalidCostAdjustedData("A/B snapshot id is invalid")
            if result.snapshot_id in indexed:
                raise _InvalidCostAdjustedData("duplicate A/B snapshot result")
            indexed[result.snapshot_id] = result
        return indexed

    @staticmethod
    def _validated_common_ids(values: tuple[int | None, ...]) -> set[int]:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in values
        ) or len(values) != len(set(values)):
            raise _InvalidCostAdjustedData("common A/B snapshot ids are invalid")
        return set(values)

    @staticmethod
    def _empty_cohort(
        cohort: RankingScenarioComparableCohort,
        definitions: tuple[RankingScenarioDefinition, ...],
        assumptions: CostAssumptions,
        status: str,
        reason: str,
        turnover_count: int,
    ) -> CostAdjustedRankingCohortResult:
        del assumptions
        empty_scenarios = tuple(
            CostAdjustedRankingScenarioResult(
                scenario_name=definition.name,
                scenario_definition_signature=definition.definition_signature,
                scenario_signature=None,
                cost_adjusted_snapshot_count=0,
                mean_baseline_gross_return=None,
                mean_scenario_gross_return=None,
                mean_gross_return_delta=None,
                mean_baseline_gross_traded_notional_ratio=None,
                mean_scenario_gross_traded_notional_ratio=None,
                mean_baseline_execution_cost_percentage=None,
                mean_scenario_execution_cost_percentage=None,
                mean_execution_cost_delta_percentage=None,
                mean_baseline_cost_adjusted_return=None,
                mean_scenario_cost_adjusted_return=None,
                mean_cost_adjusted_return_delta=None,
                median_cost_adjusted_return_delta=None,
                cost_adjusted_scenario_win_count=0,
                cost_adjusted_scenario_loss_count=0,
                cost_adjusted_tie_count=0,
                cost_adjusted_scenario_win_rate=None,
                snapshots=(),
            )
            for definition in definitions
        )
        return CostAdjustedRankingCohortResult(
            horizon_minutes=cohort.horizon_minutes,
            baseline_policy_signature=cohort.baseline_policy_signature,
            effective_top_n=cohort.effective_top_n,
            candidate_ab_snapshot_count=len(cohort.candidate_snapshot_ids),
            common_comparable_ab_snapshot_count=len(cohort.common_snapshot_ids),
            turnover_transition_count=turnover_count,
            cost_adjustable_snapshot_count=0,
            cost_adjustable_coverage_rate=Decimal("0"),
            status=status,
            safe_reason=reason,
            performance_compared=False,
            scenario_results=empty_scenarios,
        )

    @staticmethod
    def _overall_status(cohorts: tuple[CostAdjustedRankingCohortResult, ...]) -> str:
        if any(cohort.status == SUCCESS for cohort in cohorts):
            return SUCCESS
        if any(cohort.status == NO_COST_ADJUSTABLE_SNAPSHOTS for cohort in cohorts):
            return NO_COST_ADJUSTABLE_SNAPSHOTS
        if any(cohort.status == NO_TURNOVER_TRANSITIONS for cohort in cohorts):
            return NO_TURNOVER_TRANSITIONS
        return NO_COMMON_COMPARABLE_SNAPSHOTS

    @staticmethod
    def _overall_reason(status: str) -> str:
        return {
            NO_TURNOVER_TRANSITIONS: "no cohort has a matching turnover transition",
            NO_COMMON_COMPARABLE_SNAPSHOTS: "no horizon has common comparable A/B snapshots",
            NO_COST_ADJUSTABLE_SNAPSHOTS: "turnover and A/B common snapshots do not overlap",
        }[status]

    @staticmethod
    def _empty_result(
        turnover: TemporalRankingTurnoverResult,
        assumptions: CostAssumptions,
        horizons: Iterable[int],
        status: str,
        reason: str,
    ) -> CostAdjustedRankingEvaluationResult:
        horizon_values = tuple(horizons)
        return CostAdjustedRankingEvaluationResult(
            requested_snapshot_count=turnover.requested_snapshot_count,
            evaluated_snapshot_count=turnover.replayed_snapshot_count,
            scenario_count=len(turnover.scenarios),
            horizon_count=len(horizon_values),
            cohort_count=0,
            status=status,
            safe_reason=reason,
            assumptions=assumptions,
            scenarios=turnover.scenarios,
            cohorts=(),
        )
