from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import HeuristicRankingWeights
from crypto_trading_bot.services.offline_strategy_replay_service import (
    COMPONENT_WEIGHT_FIELDS,
    ReplayInputError,
    validate_weights,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    INVALID_OUTCOME_DATA,
    OUTCOME_INCOMPLETE,
    REPLAY_INCOMPATIBLE,
    SUCCESS,
    StrategyABBatchPerformanceResult,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)


SCHEMA_VERSION = "ranking-scenario-sweep-v1"
RESULT_TYPE = "LIMITED_EXPLICIT_RANKING_SCENARIO_RESEARCH"
PERFORMANCE_METRIC_TYPE = "RANKING_SELECTION_GROSS_MARKET_PERFORMANCE"
DEFINITION_SIGNATURE_PREFIX = "ranking-scenario-definition-v1"
NO_COMMON_COMPARABLE_SNAPSHOTS = "NO_COMMON_COMPARABLE_SNAPSHOTS"
INVALID_SWEEP_DATA = "INVALID_SWEEP_DATA"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_RESERVED_NAMES = frozenset({"BASELINE"})


class ScenarioDefinitionError(ReplayInputError):
    pass


@dataclass(frozen=True)
class RankingScenarioDefinition:
    name: str
    component_weights: dict[str, Decimal]
    definition_signature: str


@dataclass(frozen=True)
class RankingScenarioComparisonResult:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    raw_successful_snapshot_count: int
    raw_outcome_incomplete_count: int
    raw_baseline_integrity_failed_count: int
    raw_replay_incompatible_count: int
    raw_invalid_outcome_count: int
    common_snapshot_count: int
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
class RankingScenarioCohortResult:
    horizon_minutes: int
    baseline_policy_signature: str | None
    effective_top_n: int
    candidate_snapshot_count: int
    common_comparable_snapshot_count: int
    common_coverage_rate: Decimal
    status: str
    safe_reason: str | None
    performance_compared: bool
    scenario_results: tuple[RankingScenarioComparisonResult, ...]


@dataclass(frozen=True)
class RankingScenarioSweepResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenario_count: int
    horizon_count: int
    cohort_count: int
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[RankingScenarioCohortResult, ...]


@dataclass(frozen=True)
class RankingScenarioComparableCohort:
    """Validated per-snapshot scenario results shared by research consumers."""

    horizon_minutes: int
    baseline_policy_signature: str | None
    effective_top_n: int
    candidate_snapshot_ids: tuple[int | None, ...]
    common_snapshot_ids: tuple[int | None, ...]
    common_coverage_rate: Decimal
    status: str
    safe_reason: str | None
    scenario_results: tuple[
        tuple[str, tuple[StrategyABSnapshotPerformanceResult, ...]], ...
    ]

    def results_for(
        self, scenario_name: str
    ) -> tuple[StrategyABSnapshotPerformanceResult, ...]:
        return dict(self.scenario_results)[scenario_name]


@dataclass(frozen=True)
class RankingScenarioEvaluationMatrix:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    scenarios: tuple[RankingScenarioDefinition, ...]
    horizons: tuple[int, ...]
    cohorts: tuple[RankingScenarioComparableCohort, ...]


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _definition_signature(weights: dict[str, Decimal]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "component_weights": {
            name: _canonical_decimal(value) for name, value in sorted(weights.items())
        },
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return (
        f"{DEFINITION_SIGNATURE_PREFIX}:{sha256(canonical.encode('utf-8')).hexdigest()}"
    )


def _decimal_weight(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ScenarioDefinitionError(f"component weight is invalid: {field_name}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ScenarioDefinitionError(
            f"component weight is invalid: {field_name}"
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise ScenarioDefinitionError(f"component weight is invalid: {field_name}")
    return parsed


def parse_scenario_document(document: object) -> tuple[RankingScenarioDefinition, ...]:
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "scenarios",
    }:
        raise ScenarioDefinitionError("scenario document fields are invalid")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ScenarioDefinitionError("unsupported scenario schema version")
    raw_scenarios = document["scenarios"]
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ScenarioDefinitionError("scenarios must be a non-empty array")

    definitions: list[RankingScenarioDefinition] = []
    names: set[str] = set()
    signatures: set[str] = set()
    required_fields = set(COMPONENT_WEIGHT_FIELDS)
    for raw in raw_scenarios:
        if not isinstance(raw, dict) or set(raw) != {"name", "component_weights"}:
            raise ScenarioDefinitionError("scenario fields are invalid")
        name = raw["name"]
        if (
            not isinstance(name, str)
            or not _NAME_PATTERN.fullmatch(name)
            or name.upper() in _RESERVED_NAMES
        ):
            raise ScenarioDefinitionError("scenario name is invalid or reserved")
        if name in names:
            raise ScenarioDefinitionError(f"duplicate scenario name: {name}")
        raw_weights = raw["component_weights"]
        if not isinstance(raw_weights, dict) or set(raw_weights) != required_fields:
            raise ScenarioDefinitionError(
                "component_weights must contain the exact component field set"
            )
        weights = {
            field_name: _decimal_weight(raw_weights[field_name], field_name=field_name)
            for field_name in sorted(required_fields)
        }
        try:
            validate_weights(HeuristicRankingWeights(**weights))
        except ReplayInputError as error:
            raise ScenarioDefinitionError(str(error)) from error
        signature = _definition_signature(weights)
        if signature in signatures:
            raise ScenarioDefinitionError("duplicate scenario component weights")
        names.add(name)
        signatures.add(signature)
        definitions.append(
            RankingScenarioDefinition(
                name=name,
                component_weights=weights,
                definition_signature=signature,
            )
        )
    return tuple(definitions)


def load_scenario_file(path: str | Path) -> tuple[RankingScenarioDefinition, ...]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ScenarioDefinitionError(
            "scenario file is not valid readable JSON"
        ) from error
    return parse_scenario_document(document)


class RankingScenarioSweepService:
    """Orchestrate explicit A/B scenarios over common comparable DB-only cohorts."""

    def __init__(
        self,
        session: Session,
        *,
        performance_service: StrategyABPerformanceService | None = None,
    ) -> None:
        self.performance_service = performance_service or StrategyABPerformanceService(
            session
        )

    def evaluate(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        snapshot_id: int | None = None,
        latest: int | None = None,
    ) -> RankingScenarioSweepResult:
        matrix = self.evaluate_matrix(
            scenarios=scenarios,
            horizons=horizons,
            snapshot_id=snapshot_id,
            latest=latest,
        )
        return self.result_from_matrix(matrix)

    def result_from_matrix(
        self, matrix: RankingScenarioEvaluationMatrix
    ) -> RankingScenarioSweepResult:
        """Render the canonical sweep summary without repeating DB evaluation."""
        definitions = self._validate_definitions(matrix.scenarios)
        horizons = self._validate_horizons(matrix.horizons)
        if definitions != matrix.scenarios or horizons != matrix.horizons:
            raise ReplayInputError("scenario matrix metadata is invalid")
        cohorts = tuple(
            self._render_cohort(cohort, matrix.scenarios) for cohort in matrix.cohorts
        )
        return RankingScenarioSweepResult(
            requested_snapshot_count=matrix.requested_snapshot_count,
            evaluated_snapshot_count=matrix.evaluated_snapshot_count,
            scenario_count=len(matrix.scenarios),
            horizon_count=len(matrix.horizons),
            cohort_count=len(cohorts),
            scenarios=matrix.scenarios,
            cohorts=cohorts,
        )

    def evaluate_matrix(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        snapshot_id: int | None = None,
        latest: int | None = None,
    ) -> RankingScenarioEvaluationMatrix:
        """Evaluate and align scenarios once without calculating consumer summaries."""
        definitions = self._validate_definitions(tuple(scenarios))
        normalized_horizons = self._validate_horizons(horizons)
        if snapshot_id is not None and latest is not None:
            raise ReplayInputError("snapshot-id and latest are mutually exclusive")
        if snapshot_id is not None and (
            isinstance(snapshot_id, bool)
            or not isinstance(snapshot_id, int)
            or snapshot_id < 1
        ):
            raise ReplayInputError("snapshot-id must be >= 1")
        if latest is not None and (
            isinstance(latest, bool) or not isinstance(latest, int) or latest < 1
        ):
            raise ReplayInputError("latest snapshot count must be >= 1")
        requested_count = 1 if snapshot_id is not None else latest or 1

        results_by_horizon: dict[
            int, dict[str, tuple[StrategyABSnapshotPerformanceResult, ...]]
        ] = {}
        for horizon in normalized_horizons:
            scenario_results = {}
            for scenario in definitions:
                if snapshot_id is not None:
                    result = self.performance_service.evaluate_snapshot(
                        snapshot_id,
                        horizon_minutes=horizon,
                        overrides=dict(scenario.component_weights),
                    )
                    scenario_results[scenario.name] = (result,)
                else:
                    batch = self.performance_service.evaluate_latest(
                        requested_count,
                        horizon_minutes=horizon,
                        overrides=dict(scenario.component_weights),
                    )
                    scenario_results[scenario.name] = batch.results
            results_by_horizon[horizon] = scenario_results

        cohorts: list[RankingScenarioComparableCohort] = []
        evaluated_ids: set[int] = set()
        for horizon in normalized_horizons:
            horizon_cohorts, horizon_ids = self._build_horizon_cohorts(
                horizon, definitions, results_by_horizon[horizon]
            )
            cohorts.extend(horizon_cohorts)
            evaluated_ids.update(horizon_ids)
        return RankingScenarioEvaluationMatrix(
            requested_snapshot_count=requested_count,
            evaluated_snapshot_count=len(evaluated_ids),
            scenarios=definitions,
            horizons=normalized_horizons,
            cohorts=tuple(cohorts),
        )

    def evaluate_matrix_snapshots(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        horizons: Iterable[int],
        snapshot_ids: Iterable[int],
        outcome_as_of: datetime | None = None,
    ) -> RankingScenarioEvaluationMatrix:
        """Evaluate one explicit ordered snapshot set with an optional outcome cutoff."""
        definitions = self._validate_definitions(tuple(scenarios))
        normalized_horizons = self._validate_horizons(horizons)
        ids = tuple(snapshot_ids)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in ids
        ):
            raise ReplayInputError("snapshot IDs must be positive integers")
        if len(ids) != len(set(ids)):
            raise ReplayInputError("snapshot IDs must be unique")

        results_by_horizon = {}
        for horizon in normalized_horizons:
            scenario_results = {}
            for scenario in definitions:
                results = self.performance_service.evaluate_snapshots(
                    ids,
                    horizon_minutes=horizon,
                    overrides=dict(scenario.component_weights),
                    outcome_as_of=outcome_as_of,
                )
                if tuple(result.snapshot_id for result in results) != ids:
                    raise ReplayInputError(
                        "explicit scenario result snapshot order does not match input"
                    )
                scenario_results[scenario.name] = results
            results_by_horizon[horizon] = scenario_results

        cohorts = []
        evaluated_ids = set()
        for horizon in normalized_horizons:
            horizon_cohorts, horizon_ids = self._build_horizon_cohorts(
                horizon, definitions, results_by_horizon[horizon]
            )
            cohorts.extend(horizon_cohorts)
            evaluated_ids.update(horizon_ids)
        if evaluated_ids != set(ids):
            raise ReplayInputError("explicit matrix snapshot set does not match input")
        return RankingScenarioEvaluationMatrix(
            requested_snapshot_count=len(ids),
            evaluated_snapshot_count=len(evaluated_ids),
            scenarios=definitions,
            horizons=normalized_horizons,
            cohorts=tuple(cohorts),
        )

    @staticmethod
    def _validate_definitions(
        definitions: tuple[RankingScenarioDefinition, ...],
    ) -> tuple[RankingScenarioDefinition, ...]:
        if not definitions:
            raise ScenarioDefinitionError("at least one explicit scenario is required")
        names: set[str] = set()
        signatures: set[str] = set()
        required_fields = set(COMPONENT_WEIGHT_FIELDS)
        for definition in definitions:
            if (
                not isinstance(definition, RankingScenarioDefinition)
                or not isinstance(definition.name, str)
                or not _NAME_PATTERN.fullmatch(definition.name)
                or definition.name.upper() in _RESERVED_NAMES
                or definition.name in names
            ):
                raise ScenarioDefinitionError("scenario names must be valid and unique")
            if (
                not isinstance(definition.component_weights, dict)
                or set(definition.component_weights) != required_fields
            ):
                raise ScenarioDefinitionError(
                    "component_weights must contain the exact component field set"
                )
            weights = {
                name: _decimal_weight(value, field_name=name)
                for name, value in definition.component_weights.items()
            }
            try:
                validate_weights(HeuristicRankingWeights(**weights))
            except ReplayInputError as error:
                raise ScenarioDefinitionError(str(error)) from error
            expected_signature = _definition_signature(weights)
            if (
                definition.definition_signature != expected_signature
                or expected_signature in signatures
            ):
                raise ScenarioDefinitionError(
                    "scenario signature is invalid or duplicated"
                )
            names.add(definition.name)
            signatures.add(expected_signature)
        return definitions

    @staticmethod
    def _validate_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
        values = tuple(horizons)
        if not values:
            raise ReplayInputError("at least one horizon is required")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in values
        ):
            raise ReplayInputError("horizons must be positive integers")
        if len(values) != len(set(values)):
            raise ReplayInputError("duplicate horizons are not allowed")
        return values

    def _build_horizon_cohorts(
        self,
        horizon: int,
        scenarios: tuple[RankingScenarioDefinition, ...],
        scenario_results: dict[str, tuple[StrategyABSnapshotPerformanceResult, ...]],
    ) -> tuple[list[RankingScenarioComparableCohort], set[int]]:
        indexed = {
            name: self._index_results(results)
            for name, results in scenario_results.items()
        }
        first = indexed[scenarios[0].name]
        all_ids = set().union(*(set(results) for results in indexed.values()))
        cohort_ids: defaultdict[tuple[str | None, int], list[int | None]] = defaultdict(
            list
        )
        for snapshot_id, result in first.items():
            cohort_ids[
                (result.baseline_policy_signature, result.effective_top_n)
            ].append(snapshot_id)
        missing_from_first = all_ids - set(first)
        if missing_from_first:
            cohort_ids[(None, 0)].extend(sorted(missing_from_first, key=str))
        if not cohort_ids:
            cohort_ids[(None, 0)] = []

        cohorts = [
            self._build_cohort(
                horizon,
                key,
                tuple(ids),
                scenarios,
                indexed,
            )
            for key, ids in sorted(
                cohort_ids.items(), key=lambda item: (str(item[0][0]), item[0][1])
            )
        ]
        return cohorts, {value for value in all_ids if isinstance(value, int)}

    @staticmethod
    def _index_results(
        results: tuple[StrategyABSnapshotPerformanceResult, ...],
    ) -> dict[int | None, StrategyABSnapshotPerformanceResult]:
        indexed: dict[int | None, StrategyABSnapshotPerformanceResult] = {}
        for result in results:
            if result.snapshot_id in indexed:
                raise ReplayInputError(
                    "duplicate snapshot result in scenario evaluation"
                )
            indexed[result.snapshot_id] = result
        return indexed

    def _build_cohort(
        self,
        horizon: int,
        key: tuple[str | None, int],
        snapshot_ids: tuple[int | None, ...],
        scenarios: tuple[RankingScenarioDefinition, ...],
        indexed: dict[str, dict[int | None, StrategyABSnapshotPerformanceResult]],
    ) -> RankingScenarioComparableCohort:
        invalid_reasons: list[str] = []
        expected_ids = set(indexed[scenarios[0].name])
        for scenario in scenarios[1:]:
            if set(indexed[scenario.name]) != expected_ids:
                invalid_reasons.append("scenario snapshot sets differ")
                break
        for snapshot_id in snapshot_ids:
            rows = [indexed[item.name].get(snapshot_id) for item in scenarios]
            if any(row is None for row in rows):
                invalid_reasons.append(f"snapshot result missing: {snapshot_id}")
                continue
            metadata = {
                (
                    row.baseline_policy_signature,
                    row.effective_top_n,
                    row.pipeline_run_id,
                    row.captured_at,
                    row.baseline_top_markets,
                )
                for row in rows
            }
            if len(metadata) != 1:
                invalid_reasons.append(f"snapshot metadata mismatch: {snapshot_id}")

        common_ids = tuple(
            snapshot_id
            for snapshot_id in snapshot_ids
            if all(
                (row := indexed[scenario.name].get(snapshot_id)) is not None
                and row.status == SUCCESS
                for scenario in scenarios
            )
        )
        for snapshot_id in common_ids:
            rows = [indexed[scenario.name][snapshot_id] for scenario in scenarios]
            baseline_metrics = {
                (
                    row.baseline_mean_return,
                    row.baseline_median_return,
                    row.baseline_positive_rate,
                )
                for row in rows
            }
            if len(baseline_metrics) != 1:
                invalid_reasons.append(f"baseline metrics mismatch: {snapshot_id}")

        if invalid_reasons:
            status = INVALID_SWEEP_DATA
            safe_reason = "; ".join(sorted(set(invalid_reasons)))
            comparable_ids: tuple[int | None, ...] = ()
        elif not common_ids:
            status = NO_COMMON_COMPARABLE_SNAPSHOTS
            safe_reason = "no snapshot is SUCCESS for every explicit scenario"
            comparable_ids = ()
        else:
            status = SUCCESS
            safe_reason = None
            comparable_ids = common_ids

        candidate_count = len(snapshot_ids)
        return RankingScenarioComparableCohort(
            horizon_minutes=horizon,
            baseline_policy_signature=key[0],
            effective_top_n=key[1],
            candidate_snapshot_ids=snapshot_ids,
            common_snapshot_ids=comparable_ids,
            common_coverage_rate=(
                Decimal(len(comparable_ids)) / Decimal(candidate_count)
                if candidate_count
                else Decimal("0")
            ),
            status=status,
            safe_reason=safe_reason,
            scenario_results=tuple(
                (
                    scenario.name,
                    tuple(
                        indexed[scenario.name][snapshot_id]
                        for snapshot_id in snapshot_ids
                        if snapshot_id in indexed[scenario.name]
                    ),
                )
                for scenario in scenarios
            ),
        )

    def _render_cohort(
        self,
        cohort: RankingScenarioComparableCohort,
        scenarios: tuple[RankingScenarioDefinition, ...],
    ) -> RankingScenarioCohortResult:
        comparisons = tuple(
            self._comparison(
                scenario,
                cohort.candidate_snapshot_ids,
                cohort.common_snapshot_ids,
                {
                    result.snapshot_id: result
                    for result in cohort.results_for(scenario.name)
                },
            )
            for scenario in scenarios
        )
        return RankingScenarioCohortResult(
            horizon_minutes=cohort.horizon_minutes,
            baseline_policy_signature=cohort.baseline_policy_signature,
            effective_top_n=cohort.effective_top_n,
            candidate_snapshot_count=len(cohort.candidate_snapshot_ids),
            common_comparable_snapshot_count=len(cohort.common_snapshot_ids),
            common_coverage_rate=cohort.common_coverage_rate,
            status=cohort.status,
            safe_reason=cohort.safe_reason,
            performance_compared=cohort.status == SUCCESS,
            scenario_results=comparisons,
        )

    def _comparison(
        self,
        scenario: RankingScenarioDefinition,
        cohort_ids: tuple[int | None, ...],
        common_ids: tuple[int | None, ...],
        indexed: dict[int | None, StrategyABSnapshotPerformanceResult],
    ) -> RankingScenarioComparisonResult:
        raw = tuple(
            indexed[snapshot_id] for snapshot_id in cohort_ids if snapshot_id in indexed
        )
        common = tuple(indexed[snapshot_id] for snapshot_id in common_ids)
        aggregate: StrategyABBatchPerformanceResult = (
            self.performance_service.summarize_results(len(common), common)
        )
        return RankingScenarioComparisonResult(
            scenario_name=scenario.name,
            scenario_definition_signature=scenario.definition_signature,
            component_weights=scenario.component_weights,
            raw_successful_snapshot_count=sum(row.status == SUCCESS for row in raw),
            raw_outcome_incomplete_count=sum(
                row.status == OUTCOME_INCOMPLETE for row in raw
            ),
            raw_baseline_integrity_failed_count=sum(
                row.status == BASELINE_INTEGRITY_FAILED for row in raw
            ),
            raw_replay_incompatible_count=sum(
                row.status == REPLAY_INCOMPATIBLE for row in raw
            ),
            raw_invalid_outcome_count=sum(
                row.status == INVALID_OUTCOME_DATA for row in raw
            ),
            common_snapshot_count=len(common),
            scenario_win_count=aggregate.scenario_win_count,
            scenario_loss_count=aggregate.scenario_loss_count,
            tie_count=aggregate.tie_count,
            scenario_win_rate=aggregate.scenario_win_rate,
            mean_baseline_return=aggregate.mean_baseline_return,
            mean_scenario_return=aggregate.mean_scenario_return,
            mean_return_delta=aggregate.mean_return_delta,
            median_snapshot_return_delta=aggregate.median_snapshot_return_delta,
            mean_baseline_positive_rate=aggregate.mean_baseline_positive_rate,
            mean_scenario_positive_rate=aggregate.mean_scenario_positive_rate,
        )
