from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    OfflineStrategyReplayService,
    ReplayInputError,
    SnapshotReplayResult,
)


RESULT_TYPE = "RANKING_SELECTION_GROSS_MARKET_PERFORMANCE"
SUCCESS = "SUCCESS"
BASELINE_INTEGRITY_FAILED = "BASELINE_INTEGRITY_FAILED"
OUTCOME_INCOMPLETE = "OUTCOME_INCOMPLETE"
INVALID_OUTCOME_DATA = "INVALID_OUTCOME_DATA"
REPLAY_INCOMPATIBLE = "REPLAY_INCOMPATIBLE"


@dataclass(frozen=True)
class StrategyABSnapshotPerformanceResult:
    snapshot_id: int | None
    pipeline_run_id: str | None
    captured_at: datetime | None
    horizon_minutes: int
    baseline_policy_signature: str | None
    scenario_signature: str | None
    replay_status: str
    replay_safe_reason: str | None
    status: str
    safe_reason: str | None
    performance_evaluated: bool
    effective_top_n: int
    baseline_top_markets: tuple[str, ...]
    scenario_top_markets: tuple[str, ...]
    top_n_overlap_count: int
    top_n_overlap_rate: Decimal
    entered_top_n: tuple[str, ...]
    exited_top_n: tuple[str, ...]
    baseline_required_count: int
    scenario_required_count: int
    baseline_complete_count: int
    scenario_complete_count: int
    baseline_missing_markets: tuple[str, ...]
    scenario_missing_markets: tuple[str, ...]
    baseline_candidate_count: int
    scenario_candidate_count: int
    baseline_mean_return: Decimal | None
    scenario_mean_return: Decimal | None
    mean_return_delta: Decimal | None
    baseline_median_return: Decimal | None
    scenario_median_return: Decimal | None
    median_return_delta: Decimal | None
    baseline_positive_count: int | None
    scenario_positive_count: int | None
    baseline_negative_count: int | None
    scenario_negative_count: int | None
    baseline_flat_count: int | None
    scenario_flat_count: int | None
    baseline_positive_rate: Decimal | None
    scenario_positive_rate: Decimal | None
    positive_rate_delta: Decimal | None
    scenario_result: str | None


@dataclass(frozen=True)
class StrategyABBatchPerformanceResult:
    requested_snapshot_count: int
    evaluated_snapshot_count: int
    successful_snapshot_count: int
    outcome_incomplete_count: int
    baseline_integrity_failed_count: int
    replay_incompatible_count: int
    invalid_outcome_count: int
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
    results: tuple[StrategyABSnapshotPerformanceResult, ...]


@dataclass(frozen=True)
class _SelectionMetrics:
    mean_return: Decimal
    median_return: Decimal
    positive_count: int
    negative_count: int
    flat_count: int
    positive_rate: Decimal


_OutcomeEntry = tuple[
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome | None,
    StrategyReplaySnapshot,
]
_OutcomeMap = dict[tuple[int, str], list[_OutcomeEntry]]


class StrategyABPerformanceService:
    """Compare replayed TopN selections using stored candidate outcomes only."""

    def __init__(
        self,
        session: Session,
        *,
        replay_service: OfflineStrategyReplayService | None = None,
    ) -> None:
        self.session = session
        self.replay_service = replay_service or OfflineStrategyReplayService(session)

    def evaluate_snapshot(
        self,
        snapshot_id: int,
        *,
        horizon_minutes: int,
        overrides: dict[str, Decimal] | None = None,
    ) -> StrategyABSnapshotPerformanceResult:
        self._validate_horizon(horizon_minutes)
        replay = self.replay_service.replay_snapshot(
            snapshot_id, overrides=overrides, top_n=None
        )
        outcome_map: _OutcomeMap = {}
        if self._replay_is_evaluable(replay) and replay.snapshot_id is not None:
            outcome_map = self._load_outcome_map(
                (replay.snapshot_id,), horizon_minutes=horizon_minutes
            )
        return self._evaluate_replay(replay, horizon_minutes, outcome_map)

    def evaluate_latest(
        self,
        limit: int,
        *,
        horizon_minutes: int,
        overrides: dict[str, Decimal] | None = None,
    ) -> StrategyABBatchPerformanceResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ReplayInputError("latest snapshot count must be >= 1")
        self._validate_horizon(horizon_minutes)
        replay_batch = self.replay_service.replay_latest(
            limit, overrides=overrides, top_n=None
        )
        evaluable_ids = tuple(
            replay.snapshot_id
            for replay in replay_batch.results
            if self._replay_is_evaluable(replay) and replay.snapshot_id is not None
        )
        outcome_map = self._load_outcome_map(
            evaluable_ids, horizon_minutes=horizon_minutes
        )
        results = tuple(
            self._evaluate_replay(replay, horizon_minutes, outcome_map)
            for replay in replay_batch.results
        )
        return self._summarize(limit, results)

    def summarize_results(
        self,
        requested_count: int,
        results: Iterable[StrategyABSnapshotPerformanceResult],
    ) -> StrategyABBatchPerformanceResult:
        """Apply the existing batch semantics to an explicit result subset."""
        if (
            isinstance(requested_count, bool)
            or not isinstance(requested_count, int)
            or requested_count < 0
        ):
            raise ReplayInputError("requested snapshot count must be >= 0")
        return self._summarize(requested_count, tuple(results))

    @staticmethod
    def _validate_horizon(horizon_minutes: int) -> None:
        if (
            isinstance(horizon_minutes, bool)
            or not isinstance(horizon_minutes, int)
            or horizon_minutes < 1
        ):
            raise ReplayInputError("horizon must be a positive integer")

    @staticmethod
    def _replay_is_evaluable(replay: SnapshotReplayResult) -> bool:
        return replay.status == SUCCESS and replay.baseline_matches_stored is True

    def _load_outcome_map(
        self, snapshot_ids: Iterable[int], *, horizon_minutes: int
    ) -> _OutcomeMap:
        ids = tuple(snapshot_ids)
        if not ids:
            return {}
        query = (
            select(
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
                StrategyReplaySnapshot,
            )
            .join(
                StrategyReplaySnapshot,
                StrategyReplaySnapshot.id
                == StrategyReplayCandidate.strategy_replay_snapshot_id,
            )
            .outerjoin(
                StrategyReplayCandidateOutcome,
                and_(
                    StrategyReplayCandidateOutcome.strategy_replay_candidate_id
                    == StrategyReplayCandidate.id,
                    StrategyReplayCandidateOutcome.horizon_minutes == horizon_minutes,
                ),
            )
            .where(StrategyReplayCandidate.strategy_replay_snapshot_id.in_(ids))
            .execution_options(autoflush=False)
        )
        mapped: defaultdict[tuple[int, str], list[_OutcomeEntry]] = defaultdict(list)
        for candidate, outcome, snapshot in self.session.execute(query):
            mapped[(snapshot.id, candidate.market)].append(
                (candidate, outcome, snapshot)
            )
        return dict(mapped)

    def _evaluate_replay(
        self,
        replay: SnapshotReplayResult,
        horizon_minutes: int,
        outcome_map: _OutcomeMap,
    ) -> StrategyABSnapshotPerformanceResult:
        if not self._replay_is_evaluable(replay):
            status = (
                BASELINE_INTEGRITY_FAILED
                if replay.status == "BASELINE_MISMATCH"
                or (
                    replay.status == SUCCESS
                    and replay.baseline_matches_stored is not True
                )
                else REPLAY_INCOMPATIBLE
            )
            return self._empty_result(
                replay,
                horizon_minutes,
                status=status,
                safe_reason=self._replay_reason(replay),
            )
        if (
            len(replay.baseline_top_markets) != replay.effective_top_n
            or len(replay.scenario_top_markets) != replay.effective_top_n
        ):
            return self._empty_result(
                replay,
                horizon_minutes,
                status=REPLAY_INCOMPATIBLE,
                safe_reason="baseline and scenario TopN counts do not match",
            )

        selected_markets = set(replay.baseline_top_markets) | set(
            replay.scenario_top_markets
        )
        valid_returns: dict[str, Decimal] = {}
        incomplete_markets: set[str] = set()
        invalid_markets: set[str] = set()
        for market in selected_markets:
            entries = outcome_map.get((replay.snapshot_id, market), [])
            if len(entries) != 1:
                invalid_markets.add(market)
                continue
            candidate, outcome, snapshot = entries[0]
            if outcome is None:
                incomplete_markets.add(market)
                continue
            if not self._lineage_matches(
                replay.snapshot_id,
                horizon_minutes,
                candidate,
                outcome,
                snapshot,
            ):
                invalid_markets.add(market)
                continue
            if outcome.evaluation_status == "PARTIAL":
                incomplete_markets.add(market)
                continue
            if outcome.evaluation_status != "COMPLETE":
                invalid_markets.add(market)
                continue
            value = self._finite_decimal(outcome.market_return_percentage)
            if value is None:
                invalid_markets.add(market)
                continue
            valid_returns[market] = value

        baseline_missing = tuple(
            market
            for market in replay.baseline_top_markets
            if market not in valid_returns
        )
        scenario_missing = tuple(
            market
            for market in replay.scenario_top_markets
            if market not in valid_returns
        )
        baseline_complete = len(replay.baseline_top_markets) - len(baseline_missing)
        scenario_complete = len(replay.scenario_top_markets) - len(scenario_missing)
        if invalid_markets:
            return self._empty_result(
                replay,
                horizon_minutes,
                status=INVALID_OUTCOME_DATA,
                safe_reason=(
                    "invalid selected outcome data: "
                    + ",".join(sorted(invalid_markets))
                ),
                baseline_complete_count=baseline_complete,
                scenario_complete_count=scenario_complete,
                baseline_missing_markets=baseline_missing,
                scenario_missing_markets=scenario_missing,
            )
        if incomplete_markets or baseline_missing or scenario_missing:
            return self._empty_result(
                replay,
                horizon_minutes,
                status=OUTCOME_INCOMPLETE,
                safe_reason="selected TopN outcome coverage is incomplete",
                baseline_complete_count=baseline_complete,
                scenario_complete_count=scenario_complete,
                baseline_missing_markets=baseline_missing,
                scenario_missing_markets=scenario_missing,
            )

        baseline_values = [
            valid_returns[market] for market in replay.baseline_top_markets
        ]
        scenario_values = [
            valid_returns[market] for market in replay.scenario_top_markets
        ]
        baseline = self._metrics(baseline_values)
        scenario = self._metrics(scenario_values)
        mean_delta = scenario.mean_return - baseline.mean_return
        return StrategyABSnapshotPerformanceResult(
            snapshot_id=replay.snapshot_id,
            pipeline_run_id=replay.pipeline_run_id,
            captured_at=replay.captured_at,
            horizon_minutes=horizon_minutes,
            baseline_policy_signature=replay.baseline_policy_signature,
            scenario_signature=replay.scenario_signature,
            replay_status=replay.status,
            replay_safe_reason=replay.safe_reason,
            status=SUCCESS,
            safe_reason=None,
            performance_evaluated=True,
            effective_top_n=replay.effective_top_n,
            baseline_top_markets=replay.baseline_top_markets,
            scenario_top_markets=replay.scenario_top_markets,
            top_n_overlap_count=replay.top_n_overlap_count,
            top_n_overlap_rate=replay.top_n_overlap_rate,
            entered_top_n=replay.entered_top_n,
            exited_top_n=replay.exited_top_n,
            baseline_required_count=len(replay.baseline_top_markets),
            scenario_required_count=len(replay.scenario_top_markets),
            baseline_complete_count=len(baseline_values),
            scenario_complete_count=len(scenario_values),
            baseline_missing_markets=(),
            scenario_missing_markets=(),
            baseline_candidate_count=len(baseline_values),
            scenario_candidate_count=len(scenario_values),
            baseline_mean_return=baseline.mean_return,
            scenario_mean_return=scenario.mean_return,
            mean_return_delta=mean_delta,
            baseline_median_return=baseline.median_return,
            scenario_median_return=scenario.median_return,
            median_return_delta=scenario.median_return - baseline.median_return,
            baseline_positive_count=baseline.positive_count,
            scenario_positive_count=scenario.positive_count,
            baseline_negative_count=baseline.negative_count,
            scenario_negative_count=scenario.negative_count,
            baseline_flat_count=baseline.flat_count,
            scenario_flat_count=scenario.flat_count,
            baseline_positive_rate=baseline.positive_rate,
            scenario_positive_rate=scenario.positive_rate,
            positive_rate_delta=scenario.positive_rate - baseline.positive_rate,
            scenario_result=(
                "SCENARIO_WIN"
                if mean_delta > 0
                else "SCENARIO_LOSS"
                if mean_delta < 0
                else "TIE"
            ),
        )

    @staticmethod
    def _lineage_matches(
        snapshot_id: int | None,
        horizon_minutes: int,
        candidate: StrategyReplayCandidate,
        outcome: StrategyReplayCandidateOutcome,
        snapshot: StrategyReplaySnapshot,
    ) -> bool:
        return (
            snapshot_id is not None
            and snapshot.id == snapshot_id
            and candidate.strategy_replay_snapshot_id == snapshot_id
            and candidate.analysis_run_id == snapshot.analysis_run_id
            and candidate.user_id == snapshot.user_id
            and candidate.exchange == snapshot.exchange
            and outcome.strategy_replay_candidate_id == candidate.id
            and outcome.strategy_replay_snapshot_id == snapshot_id
            and outcome.user_id == candidate.user_id
            and outcome.exchange == candidate.exchange
            and outcome.market == candidate.market
            and outcome.horizon_minutes == horizon_minutes
        )

    @staticmethod
    def _finite_decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _metrics(values: list[Decimal]) -> _SelectionMetrics:
        count = len(values)
        positive = sum(value > 0 for value in values)
        negative = sum(value < 0 for value in values)
        flat = count - positive - negative
        return _SelectionMetrics(
            mean_return=sum(values, Decimal("0")) / Decimal(count),
            median_return=median(values),
            positive_count=positive,
            negative_count=negative,
            flat_count=flat,
            positive_rate=Decimal(positive) / Decimal(count),
        )

    @staticmethod
    def _replay_reason(replay: SnapshotReplayResult) -> str:
        reason = replay.safe_reason or "unspecified replay incompatibility"
        return f"offline replay status={replay.status}: {reason}"

    @staticmethod
    def _empty_result(
        replay: SnapshotReplayResult,
        horizon_minutes: int,
        *,
        status: str,
        safe_reason: str,
        baseline_complete_count: int = 0,
        scenario_complete_count: int = 0,
        baseline_missing_markets: tuple[str, ...] | None = None,
        scenario_missing_markets: tuple[str, ...] | None = None,
    ) -> StrategyABSnapshotPerformanceResult:
        baseline_markets = replay.baseline_top_markets
        scenario_markets = replay.scenario_top_markets
        return StrategyABSnapshotPerformanceResult(
            snapshot_id=replay.snapshot_id,
            pipeline_run_id=replay.pipeline_run_id,
            captured_at=replay.captured_at,
            horizon_minutes=horizon_minutes,
            baseline_policy_signature=replay.baseline_policy_signature,
            scenario_signature=replay.scenario_signature,
            replay_status=replay.status,
            replay_safe_reason=replay.safe_reason,
            status=status,
            safe_reason=safe_reason,
            performance_evaluated=False,
            effective_top_n=replay.effective_top_n,
            baseline_top_markets=baseline_markets,
            scenario_top_markets=scenario_markets,
            top_n_overlap_count=replay.top_n_overlap_count,
            top_n_overlap_rate=replay.top_n_overlap_rate,
            entered_top_n=replay.entered_top_n,
            exited_top_n=replay.exited_top_n,
            baseline_required_count=len(baseline_markets),
            scenario_required_count=len(scenario_markets),
            baseline_complete_count=baseline_complete_count,
            scenario_complete_count=scenario_complete_count,
            baseline_missing_markets=(
                baseline_markets
                if baseline_missing_markets is None
                else baseline_missing_markets
            ),
            scenario_missing_markets=(
                scenario_markets
                if scenario_missing_markets is None
                else scenario_missing_markets
            ),
            baseline_candidate_count=len(baseline_markets),
            scenario_candidate_count=len(scenario_markets),
            baseline_mean_return=None,
            scenario_mean_return=None,
            mean_return_delta=None,
            baseline_median_return=None,
            scenario_median_return=None,
            median_return_delta=None,
            baseline_positive_count=None,
            scenario_positive_count=None,
            baseline_negative_count=None,
            scenario_negative_count=None,
            baseline_flat_count=None,
            scenario_flat_count=None,
            baseline_positive_rate=None,
            scenario_positive_rate=None,
            positive_rate_delta=None,
            scenario_result=None,
        )

    @staticmethod
    def _summarize(
        requested_count: int,
        results: tuple[StrategyABSnapshotPerformanceResult, ...],
    ) -> StrategyABBatchPerformanceResult:
        successful = tuple(result for result in results if result.status == SUCCESS)
        wins = sum(result.scenario_result == "SCENARIO_WIN" for result in successful)
        losses = sum(result.scenario_result == "SCENARIO_LOSS" for result in successful)
        ties = sum(result.scenario_result == "TIE" for result in successful)

        def mean_of(attribute: str) -> Decimal | None:
            values = [getattr(result, attribute) for result in successful]
            return sum(values, Decimal("0")) / Decimal(len(values)) if values else None

        deltas = [result.mean_return_delta for result in successful]
        return StrategyABBatchPerformanceResult(
            requested_snapshot_count=requested_count,
            evaluated_snapshot_count=len(results),
            successful_snapshot_count=len(successful),
            outcome_incomplete_count=sum(
                result.status == OUTCOME_INCOMPLETE for result in results
            ),
            baseline_integrity_failed_count=sum(
                result.status == BASELINE_INTEGRITY_FAILED for result in results
            ),
            replay_incompatible_count=sum(
                result.status == REPLAY_INCOMPATIBLE for result in results
            ),
            invalid_outcome_count=sum(
                result.status == INVALID_OUTCOME_DATA for result in results
            ),
            scenario_win_count=wins,
            scenario_loss_count=losses,
            tie_count=ties,
            scenario_win_rate=(
                Decimal(wins) / Decimal(len(successful)) if successful else None
            ),
            mean_baseline_return=mean_of("baseline_mean_return"),
            mean_scenario_return=mean_of("scenario_mean_return"),
            mean_return_delta=mean_of("mean_return_delta"),
            median_snapshot_return_delta=median(deltas) if deltas else None,
            mean_baseline_positive_rate=mean_of("baseline_positive_rate"),
            mean_scenario_positive_rate=mean_of("scenario_positive_rate"),
            results=results,
        )
