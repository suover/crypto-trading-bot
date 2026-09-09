from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from typing import Iterable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.offline_strategy_replay_service import (
    BatchReplayResult,
    OfflineStrategyReplayService,
    ReplayInputError,
    SnapshotReplayResult,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioDefinition,
    RankingScenarioSweepService,
)


RESULT_TYPE = "TEMPORAL_TOP_N_SELECTION_REPLACEMENT_RESEARCH"
SUCCESS = "SUCCESS"
NO_REPLAY_SNAPSHOTS = "NO_REPLAY_SNAPSHOTS"
NO_COMMON_REPLAYABLE_SNAPSHOTS = "NO_COMMON_REPLAYABLE_SNAPSHOTS"
INSUFFICIENT_TEMPORAL_TRANSITIONS = "INSUFFICIENT_TEMPORAL_TRANSITIONS"
INVALID_TURNOVER_DATA = "INVALID_TURNOVER_DATA"


@dataclass(frozen=True)
class RankingSelectionTransition:
    previous_snapshot_id: int
    current_snapshot_id: int
    previous_captured_at: datetime
    current_captured_at: datetime
    effective_top_n: int
    previous_top_markets: tuple[str, ...]
    current_top_markets: tuple[str, ...]
    retained_markets: tuple[str, ...]
    entered_markets: tuple[str, ...]
    exited_markets: tuple[str, ...]
    retained_count: int
    entered_count: int
    exited_count: int
    retention_rate: Decimal
    replacement_rate: Decimal


@dataclass(frozen=True)
class TemporalRankingTurnoverScenarioTransition:
    scenario_name: str
    scenario_signature: str
    transition: RankingSelectionTransition
    replacement_rate_delta_vs_baseline: Decimal


@dataclass(frozen=True)
class TemporalRankingTurnoverTransition:
    transition_index: int
    baseline: RankingSelectionTransition
    scenarios: tuple[TemporalRankingTurnoverScenarioTransition, ...]


@dataclass(frozen=True)
class RankingSelectionTurnoverSummary:
    transition_count: int
    total_entered_count: int
    total_exited_count: int
    mean_replacement_rate: Decimal | None
    median_replacement_rate: Decimal | None
    min_replacement_rate: Decimal | None
    max_replacement_rate: Decimal | None
    mean_retention_rate: Decimal | None
    median_retention_rate: Decimal | None
    zero_replacement_transition_count: int
    full_replacement_transition_count: int


@dataclass(frozen=True)
class TemporalRankingTurnoverScenarioSummary:
    scenario_name: str
    scenario_definition_signature: str
    scenario_signature: str
    summary: RankingSelectionTurnoverSummary
    mean_replacement_rate_delta_vs_baseline: Decimal | None
    median_replacement_rate_delta_vs_baseline: Decimal | None


@dataclass(frozen=True)
class TemporalRankingTurnoverCohortResult:
    baseline_policy_signature: str
    effective_top_n: int
    candidate_snapshot_ids: tuple[int, ...]
    common_replayable_snapshot_ids: tuple[int, ...]
    candidate_snapshot_count: int
    common_replayable_snapshot_count: int
    common_coverage_rate: Decimal
    transition_count: int
    continuity_break_count: int
    status: str
    safe_reason: str | None
    turnover_compared: bool
    transitions: tuple[TemporalRankingTurnoverTransition, ...]
    baseline_summary: RankingSelectionTurnoverSummary
    scenario_summaries: tuple[TemporalRankingTurnoverScenarioSummary, ...]


@dataclass(frozen=True)
class TemporalRankingTurnoverResult:
    requested_snapshot_count: int
    replayed_snapshot_count: int
    scenario_count: int
    cohort_count: int
    status: str
    safe_reason: str | None
    chronology: str
    research_only: bool
    outcome_data_used: bool
    policy_decision_performed: bool
    scenarios: tuple[RankingScenarioDefinition, ...]
    cohorts: tuple[TemporalRankingTurnoverCohortResult, ...]


class _InvalidTurnoverData(Exception):
    pass


class TemporalRankingTurnoverService:
    """Describe chronological Top-N membership changes from persisted replay data."""

    def __init__(
        self,
        session: Session,
        *,
        replay_service: OfflineStrategyReplayService | None = None,
    ) -> None:
        self.replay_service = replay_service or OfflineStrategyReplayService(session)

    def evaluate(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        latest: int,
    ) -> TemporalRankingTurnoverResult:
        definitions = RankingScenarioSweepService._validate_definitions(
            tuple(scenarios)
        )
        if isinstance(latest, bool) or not isinstance(latest, int) or latest < 1:
            raise ReplayInputError("latest snapshot count must be >= 1")

        batches = {
            definition.name: self.replay_service.replay_latest(
                latest, overrides=dict(definition.component_weights), top_n=None
            )
            for definition in definitions
        }
        return self._evaluate_replays(definitions, latest, batches)

    def evaluate_snapshots(
        self,
        *,
        scenarios: Iterable[RankingScenarioDefinition],
        snapshot_ids: Iterable[int],
    ) -> TemporalRankingTurnoverResult:
        """Evaluate one explicit timeline without removing continuity breaks."""
        definitions = RankingScenarioSweepService._validate_definitions(
            tuple(scenarios)
        )
        ids = tuple(snapshot_ids)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in ids
        ):
            raise ReplayInputError("snapshot IDs must be positive integers")
        if len(ids) != len(set(ids)):
            raise ReplayInputError("snapshot IDs must be unique")
        batches = {
            definition.name: self.replay_service.replay_snapshots(
                ids, overrides=dict(definition.component_weights), top_n=None
            )
            for definition in definitions
        }
        return self._evaluate_replays(definitions, len(ids), batches)

    def _evaluate_replays(
        self,
        definitions: tuple[RankingScenarioDefinition, ...],
        requested_count: int,
        batches: dict[str, BatchReplayResult],
    ) -> TemporalRankingTurnoverResult:
        try:
            return self._evaluate_batches(definitions, requested_count, batches)
        except _InvalidTurnoverData as error:
            replayed_count = max(
                (batch.replayed_snapshot_count for batch in batches.values()),
                default=0,
            )
            return self._result(
                definitions,
                requested_count,
                replayed_count,
                INVALID_TURNOVER_DATA,
                str(error),
                (),
            )

    def _evaluate_batches(
        self,
        definitions: tuple[RankingScenarioDefinition, ...],
        latest: int,
        batches: dict[str, BatchReplayResult],
    ) -> TemporalRankingTurnoverResult:
        indexed: dict[str, dict[int, SnapshotReplayResult]] = {}
        for definition in definitions:
            batch = batches[definition.name]
            results = batch.results
            if batch.requested_snapshot_count != latest:
                raise _InvalidTurnoverData("replay requested count mismatch")
            if batch.replayed_snapshot_count != len(results):
                raise _InvalidTurnoverData("replay result count mismatch")
            scenario_index: dict[int, SnapshotReplayResult] = {}
            for result in results:
                if (
                    isinstance(result.snapshot_id, bool)
                    or not isinstance(result.snapshot_id, int)
                    or result.snapshot_id < 1
                ):
                    raise _InvalidTurnoverData("snapshot id is missing or invalid")
                if result.snapshot_id in scenario_index:
                    raise _InvalidTurnoverData(
                        "duplicate snapshot id in replay results"
                    )
                scenario_index[result.snapshot_id] = result
            indexed[definition.name] = scenario_index

        first_name = definitions[0].name
        first = indexed[first_name]
        expected_ids = set(first)
        if any(
            set(indexed[definition.name]) != expected_ids for definition in definitions
        ):
            raise _InvalidTurnoverData("scenario replay snapshot sets differ")
        if not expected_ids:
            return self._result(
                definitions, latest, 0, NO_REPLAY_SNAPSHOTS, "no replay snapshots", ()
            )

        ordered_ids = tuple(
            sorted(
                expected_ids,
                key=lambda snapshot_id: self._chronology_key(first[snapshot_id]),
            )
        )
        self._validate_cross_scenario_metadata(definitions, indexed, ordered_ids)

        common_ids: set[int] = set()
        for snapshot_id in ordered_ids:
            results = tuple(
                indexed[definition.name][snapshot_id] for definition in definitions
            )
            if all(self._is_common_replayable(result) for result in results):
                for result in results:
                    self._validate_replayable_snapshot(result)
                common_ids.add(snapshot_id)
        if not common_ids:
            return self._result(
                definitions,
                latest,
                len(ordered_ids),
                NO_COMMON_REPLAYABLE_SNAPSHOTS,
                "no snapshot passed every scenario replay and baseline integrity check",
                (),
            )

        cohort_ids: defaultdict[tuple[str, int], list[int]] = defaultdict(list)
        for snapshot_id in ordered_ids:
            available_keys = {
                (result.baseline_policy_signature, result.effective_top_n)
                for definition in definitions
                if (
                    (
                        result := indexed[definition.name][snapshot_id]
                    ).baseline_policy_signature
                    and isinstance(result.effective_top_n, int)
                    and not isinstance(result.effective_top_n, bool)
                    and result.effective_top_n > 0
                )
            }
            if len(available_keys) > 1:
                raise _InvalidTurnoverData(
                    f"cross-scenario cohort metadata mismatch: snapshot_id={snapshot_id}"
                )
            if available_keys:
                cohort_ids[available_keys.pop()].append(snapshot_id)

        cohorts = tuple(
            self._build_cohort(
                key,
                candidate_ids,
                ordered_ids,
                common_ids,
                definitions,
                indexed,
            )
            for key, candidate_ids in sorted(cohort_ids.items())
            if any(snapshot_id in common_ids for snapshot_id in candidate_ids)
        )
        if not cohorts:
            return self._result(
                definitions,
                latest,
                len(ordered_ids),
                NO_COMMON_REPLAYABLE_SNAPSHOTS,
                "common snapshots lack a valid cohort key",
                (),
            )
        status = (
            SUCCESS
            if any(cohort.status == SUCCESS for cohort in cohorts)
            else INSUFFICIENT_TEMPORAL_TRANSITIONS
        )
        reason = (
            None
            if status == SUCCESS
            else "no cohort has an adjacent common replayable snapshot pair"
        )
        return self._result(
            definitions, latest, len(ordered_ids), status, reason, cohorts
        )

    @staticmethod
    def _chronology_key(result: SnapshotReplayResult) -> tuple[datetime, int]:
        captured_at = result.captured_at
        if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
            raise _InvalidTurnoverData("captured_at must be timezone-aware")
        return captured_at.astimezone(UTC), result.snapshot_id

    @staticmethod
    def _validate_cross_scenario_metadata(
        definitions: tuple[RankingScenarioDefinition, ...],
        indexed: dict[str, dict[int, SnapshotReplayResult]],
        ordered_ids: tuple[int, ...],
    ) -> None:
        identity_fields = (
            "snapshot_id",
            "pipeline_run_id",
            "captured_at",
            "baseline_policy_signature",
        )
        first_name = definitions[0].name
        for snapshot_id in ordered_ids:
            reference = indexed[first_name][snapshot_id]
            for definition in definitions[1:]:
                current = indexed[definition.name][snapshot_id]
                if any(
                    getattr(current, field) != getattr(reference, field)
                    for field in identity_fields
                ):
                    raise _InvalidTurnoverData(
                        f"cross-scenario metadata mismatch: snapshot_id={snapshot_id}"
                    )
            results = tuple(
                indexed[definition.name][snapshot_id] for definition in definitions
            )
            available_baselines = {
                (result.effective_top_n, result.baseline_top_markets)
                for result in results
                if result.effective_top_n > 0 and result.baseline_top_markets
            }
            if len(available_baselines) > 1:
                raise _InvalidTurnoverData(
                    f"cross-scenario baseline metadata mismatch: snapshot_id={snapshot_id}"
                )

    @staticmethod
    def _is_common_replayable(result: SnapshotReplayResult) -> bool:
        return result.status == SUCCESS and result.baseline_matches_stored is True

    @classmethod
    def _validate_replayable_snapshot(cls, result: SnapshotReplayResult) -> None:
        if (
            not isinstance(result.baseline_policy_signature, str)
            or not result.baseline_policy_signature
            or isinstance(result.effective_top_n, bool)
            or not isinstance(result.effective_top_n, int)
            or result.effective_top_n < 1
            or not isinstance(result.scenario_signature, str)
            or not result.scenario_signature
        ):
            raise _InvalidTurnoverData("replayable snapshot metadata is invalid")
        cls._validate_top_markets(result.baseline_top_markets, result.effective_top_n)
        cls._validate_top_markets(result.scenario_top_markets, result.effective_top_n)

    @staticmethod
    def _validate_top_markets(values: tuple[str, ...], top_n: int) -> None:
        if (
            len(values) != top_n
            or len(set(values)) != top_n
            or any(not isinstance(market, str) or not market for market in values)
        ):
            raise _InvalidTurnoverData("Top-N membership is invalid")

    def _build_cohort(
        self,
        key: tuple[str, int],
        candidate_ids: list[int],
        ordered_ids: tuple[int, ...],
        common_ids: set[int],
        definitions: tuple[RankingScenarioDefinition, ...],
        indexed: dict[str, dict[int, SnapshotReplayResult]],
    ) -> TemporalRankingTurnoverCohortResult:
        signature, top_n = key
        scenario_signatures: dict[str, str] = {}
        for definition in definitions:
            signatures = {
                indexed[definition.name][snapshot_id].scenario_signature
                for snapshot_id in candidate_ids
                if snapshot_id in common_ids
            }
            if len(signatures) != 1 or None in signatures:
                raise _InvalidTurnoverData(
                    f"scenario signature is inconsistent: scenario={definition.name}"
                )
            scenario_signatures[definition.name] = signatures.pop()

        candidate_set = set(candidate_ids)
        transitions: list[TemporalRankingTurnoverTransition] = []
        continuity_break_count = 0
        for previous_id, current_id in zip(ordered_ids, ordered_ids[1:], strict=False):
            touches_cohort = previous_id in candidate_set or current_id in candidate_set
            pair_is_valid = (
                previous_id in candidate_set
                and current_id in candidate_set
                and previous_id in common_ids
                and current_id in common_ids
            )
            if touches_cohort and not pair_is_valid:
                continuity_break_count += 1
            if not pair_is_valid:
                continue
            baseline = self._transition(
                indexed[definitions[0].name][previous_id],
                indexed[definitions[0].name][current_id],
                top_n=top_n,
                use_baseline=True,
            )
            scenario_transitions = tuple(
                TemporalRankingTurnoverScenarioTransition(
                    scenario_name=definition.name,
                    scenario_signature=scenario_signatures[definition.name],
                    transition=(
                        scenario_transition := self._transition(
                            indexed[definition.name][previous_id],
                            indexed[definition.name][current_id],
                            top_n=top_n,
                            use_baseline=False,
                        )
                    ),
                    replacement_rate_delta_vs_baseline=(
                        scenario_transition.replacement_rate - baseline.replacement_rate
                    ),
                )
                for definition in definitions
            )
            transitions.append(
                TemporalRankingTurnoverTransition(
                    transition_index=len(transitions) + 1,
                    baseline=baseline,
                    scenarios=scenario_transitions,
                )
            )

        baseline_summary = self._summary(
            tuple(transition.baseline for transition in transitions)
        )
        scenario_summaries = tuple(
            self._scenario_summary(
                definition,
                scenario_signatures[definition.name],
                tuple(
                    scenario
                    for transition in transitions
                    for scenario in transition.scenarios
                    if scenario.scenario_name == definition.name
                ),
            )
            for definition in definitions
        )
        common_count = sum(snapshot_id in common_ids for snapshot_id in candidate_ids)
        common_snapshot_ids = tuple(
            snapshot_id for snapshot_id in candidate_ids if snapshot_id in common_ids
        )
        status = SUCCESS if transitions else INSUFFICIENT_TEMPORAL_TRANSITIONS
        return TemporalRankingTurnoverCohortResult(
            baseline_policy_signature=signature,
            effective_top_n=top_n,
            candidate_snapshot_ids=tuple(candidate_ids),
            common_replayable_snapshot_ids=common_snapshot_ids,
            candidate_snapshot_count=len(candidate_ids),
            common_replayable_snapshot_count=common_count,
            common_coverage_rate=Decimal(common_count) / Decimal(len(candidate_ids)),
            transition_count=len(transitions),
            continuity_break_count=continuity_break_count,
            status=status,
            safe_reason=(
                None
                if transitions
                else "cohort has no adjacent common replayable snapshot pair"
            ),
            turnover_compared=bool(transitions),
            transitions=tuple(transitions),
            baseline_summary=baseline_summary,
            scenario_summaries=scenario_summaries,
        )

    @staticmethod
    def _transition(
        previous: SnapshotReplayResult,
        current: SnapshotReplayResult,
        *,
        top_n: int,
        use_baseline: bool,
    ) -> RankingSelectionTransition:
        previous_markets = (
            previous.baseline_top_markets
            if use_baseline
            else previous.scenario_top_markets
        )
        current_markets = (
            current.baseline_top_markets
            if use_baseline
            else current.scenario_top_markets
        )
        for values in (previous_markets, current_markets):
            TemporalRankingTurnoverService._validate_top_markets(values, top_n)
        current_set = set(current_markets)
        previous_set = set(previous_markets)
        retained = tuple(market for market in previous_markets if market in current_set)
        exited = tuple(
            market for market in previous_markets if market not in current_set
        )
        entered = tuple(
            market for market in current_markets if market not in previous_set
        )
        if len(entered) != len(exited):
            raise _InvalidTurnoverData("Top-N entered/exited counts are inconsistent")
        retained_count = len(retained)
        entered_count = len(entered)
        denominator = Decimal(top_n)
        return RankingSelectionTransition(
            previous_snapshot_id=previous.snapshot_id,
            current_snapshot_id=current.snapshot_id,
            previous_captured_at=previous.captured_at.astimezone(UTC),
            current_captured_at=current.captured_at.astimezone(UTC),
            effective_top_n=top_n,
            previous_top_markets=previous_markets,
            current_top_markets=current_markets,
            retained_markets=retained,
            entered_markets=entered,
            exited_markets=exited,
            retained_count=retained_count,
            entered_count=entered_count,
            exited_count=len(exited),
            retention_rate=Decimal(retained_count) / denominator,
            replacement_rate=Decimal(entered_count) / denominator,
        )

    @staticmethod
    def _summary(
        transitions: tuple[RankingSelectionTransition, ...],
    ) -> RankingSelectionTurnoverSummary:
        replacements = tuple(item.replacement_rate for item in transitions)
        retentions = tuple(item.retention_rate for item in transitions)
        count = len(transitions)
        return RankingSelectionTurnoverSummary(
            transition_count=count,
            total_entered_count=sum(item.entered_count for item in transitions),
            total_exited_count=sum(item.exited_count for item in transitions),
            mean_replacement_rate=(
                sum(replacements, Decimal("0")) / count if count else None
            ),
            median_replacement_rate=median(replacements) if count else None,
            min_replacement_rate=min(replacements) if count else None,
            max_replacement_rate=max(replacements) if count else None,
            mean_retention_rate=(
                sum(retentions, Decimal("0")) / count if count else None
            ),
            median_retention_rate=median(retentions) if count else None,
            zero_replacement_transition_count=sum(value == 0 for value in replacements),
            full_replacement_transition_count=sum(value == 1 for value in replacements),
        )

    @classmethod
    def _scenario_summary(
        cls,
        definition: RankingScenarioDefinition,
        scenario_signature: str,
        transitions: tuple[TemporalRankingTurnoverScenarioTransition, ...],
    ) -> TemporalRankingTurnoverScenarioSummary:
        deltas = tuple(item.replacement_rate_delta_vs_baseline for item in transitions)
        return TemporalRankingTurnoverScenarioSummary(
            scenario_name=definition.name,
            scenario_definition_signature=definition.definition_signature,
            scenario_signature=scenario_signature,
            summary=cls._summary(tuple(item.transition for item in transitions)),
            mean_replacement_rate_delta_vs_baseline=(
                sum(deltas, Decimal("0")) / len(deltas) if deltas else None
            ),
            median_replacement_rate_delta_vs_baseline=(
                median(deltas) if deltas else None
            ),
        )

    @staticmethod
    def _result(
        definitions: tuple[RankingScenarioDefinition, ...],
        requested_count: int,
        replayed_count: int,
        status: str,
        reason: str | None,
        cohorts: tuple[TemporalRankingTurnoverCohortResult, ...],
    ) -> TemporalRankingTurnoverResult:
        return TemporalRankingTurnoverResult(
            requested_snapshot_count=requested_count,
            replayed_snapshot_count=replayed_count,
            scenario_count=len(definitions),
            cohort_count=len(cohorts),
            status=status,
            safe_reason=reason,
            chronology="captured_at_utc_asc_then_snapshot_id_asc",
            research_only=True,
            outcome_data_used=False,
            policy_decision_performed=False,
            scenarios=definitions,
            cohorts=cohorts,
        )
