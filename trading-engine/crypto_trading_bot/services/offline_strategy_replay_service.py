from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    HeuristicRankingWeights,
)
from crypto_trading_bot.db.models import (
    StrategyReplayCandidate,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    policy_signature,
)


SUPPORTED_RANKING_POLICY = HeuristicMarketRankingPolicy.__name__
SCENARIO_SIGNATURE_PREFIX = "offline-replay-v1"
SCORE_TOLERANCE = Decimal("0.0000000005")
WEIGHT_FIELDS = tuple(field.name for field in fields(HeuristicRankingWeights))
REFERENCE_FIELDS = frozenset(
    field_name for field_name in WEIGHT_FIELDS if "reference" in field_name
)
COMPONENT_WEIGHT_FIELDS = frozenset(WEIGHT_FIELDS) - REFERENCE_FIELDS


class ReplayInputError(ValueError):
    pass


class _IncompatibleSnapshotError(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class BaselineMismatchDiagnostic:
    market: str
    stored_original_rank: int
    replay_baseline_rank: int
    stored_original_score: Decimal
    replay_baseline_score: Decimal
    score_delta: Decimal


@dataclass(frozen=True)
class CandidateReplayResult:
    market: str
    original_rank: int
    baseline_replay_rank: int
    scenario_rank: int
    original_score: Decimal
    baseline_replay_score: Decimal
    scenario_score: Decimal
    rank_delta_vs_original: int
    score_delta_vs_original: Decimal
    original_final_selected: bool
    original_ranked_selected: bool
    scenario_selected: bool
    entered_top_n: bool
    exited_top_n: bool


@dataclass(frozen=True)
class SnapshotReplayResult:
    snapshot_id: int | None
    pipeline_run_id: str | None
    captured_at: datetime | None
    dataset_schema_version: str | None
    baseline_policy_signature: str | None
    scenario_signature: str | None
    status: str
    safe_reason: str | None
    rankable_candidate_count: int
    stored_top_n: int | None
    requested_top_n: int | None
    effective_top_n: int
    held_augmented_count: int
    baseline_matches_stored: bool
    baseline_top_markets: tuple[str, ...]
    scenario_top_markets: tuple[str, ...]
    top_n_overlap_count: int
    top_n_overlap_rate: Decimal
    entered_top_n: tuple[str, ...]
    exited_top_n: tuple[str, ...]
    candidate_results: tuple[CandidateReplayResult, ...]
    mismatch_diagnostics: tuple[BaselineMismatchDiagnostic, ...]

    @property
    def compatible(self) -> bool:
        return self.status in {"SUCCESS", "BASELINE_MISMATCH"}


@dataclass(frozen=True)
class BatchReplayResult:
    requested_snapshot_count: int
    replayed_snapshot_count: int
    compatible_snapshot_count: int
    incompatible_snapshot_count: int
    baseline_match_count: int
    baseline_mismatch_count: int
    mean_top_n_overlap_rate: Decimal
    mean_absolute_rank_change: Decimal
    total_entered_top_n: int
    total_exited_top_n: int
    results: tuple[SnapshotReplayResult, ...]


def _decimal(value: object, *, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ReplayInputError(f"{field_name} must be a valid Decimal") from error
    if not result.is_finite():
        raise ReplayInputError(f"{field_name} must be finite")
    return result


def parse_override(value: str) -> tuple[str, Decimal]:
    field_name, separator, raw_value = value.partition("=")
    if not separator or not field_name or not raw_value:
        raise ReplayInputError("override must use FIELD=VALUE")
    if field_name not in WEIGHT_FIELDS:
        raise ReplayInputError(f"unknown ranking override field: {field_name}")
    return field_name, _decimal(raw_value, field_name=field_name)


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def validate_weights(weights: HeuristicRankingWeights) -> None:
    for field_name in COMPONENT_WEIGHT_FIELDS:
        value = getattr(weights, field_name)
        if not value.is_finite() or value < 0:
            raise ReplayInputError(
                f"ranking component weight must be finite and >= 0: {field_name}"
            )
    component_total = sum(
        (getattr(weights, field_name) for field_name in COMPONENT_WEIGHT_FIELDS),
        start=Decimal("0"),
    )
    if component_total != Decimal("1"):
        raise ReplayInputError("ranking component weights must sum to 1")
    for field_name in REFERENCE_FIELDS:
        value = getattr(weights, field_name)
        if not value.is_finite() or value <= 0:
            raise ReplayInputError(
                f"ranking reference parameter must be finite and > 0: {field_name}"
            )


def restore_weights(policy_data: object) -> tuple[HeuristicRankingWeights, int]:
    if not isinstance(policy_data, dict):
        raise _IncompatibleSnapshotError(
            "INVALID_REPLAY_DATA", "policy_data must be an object"
        )
    ranking = policy_data.get("ranking")
    universe = policy_data.get("market_universe")
    if not isinstance(ranking, dict) or not isinstance(universe, dict):
        raise _IncompatibleSnapshotError(
            "INVALID_REPLAY_DATA", "stored ranking or market_universe policy is missing"
        )
    if ranking.get("policy_name") != SUPPORTED_RANKING_POLICY:
        raise _IncompatibleSnapshotError(
            "UNSUPPORTED_RANKING_POLICY",
            f"unsupported ranking policy: {ranking.get('policy_name')}",
        )
    raw_weights = ranking.get("weights")
    if not isinstance(raw_weights, dict) or set(raw_weights) != set(WEIGHT_FIELDS):
        raise _IncompatibleSnapshotError(
            "INVALID_REPLAY_DATA",
            "stored ranking weights do not match HeuristicRankingWeights fields",
        )
    try:
        values = {
            field_name: _decimal(raw_weights[field_name], field_name=field_name)
            for field_name in WEIGHT_FIELDS
        }
        weights = HeuristicRankingWeights(**values)
        validate_weights(weights)
    except ReplayInputError as error:
        raise _IncompatibleSnapshotError("INVALID_REPLAY_DATA", str(error)) from error
    stored_top_n = universe.get("top_n")
    if isinstance(stored_top_n, bool) or not isinstance(stored_top_n, int):
        raise _IncompatibleSnapshotError(
            "INVALID_REPLAY_DATA", "stored market_universe.top_n must be an integer"
        )
    if stored_top_n < 1:
        raise _IncompatibleSnapshotError(
            "INVALID_REPLAY_DATA", "stored market_universe.top_n must be >= 1"
        )
    return weights, stored_top_n


def apply_overrides(
    baseline: HeuristicRankingWeights,
    overrides: dict[str, Decimal] | None,
) -> HeuristicRankingWeights:
    overrides = overrides or {}
    unknown = set(overrides) - set(WEIGHT_FIELDS)
    if unknown:
        raise ReplayInputError(f"unknown ranking override field: {sorted(unknown)[0]}")
    normalized = {
        field_name: _decimal(value, field_name=field_name)
        for field_name, value in overrides.items()
    }
    result = replace(baseline, **normalized)
    validate_weights(result)
    return result


def scenario_signature(weights: HeuristicRankingWeights, requested_top_n: int) -> str:
    payload = {
        "ranking": {
            "policy_name": SUPPORTED_RANKING_POLICY,
            "weights": {
                key: _canonical_decimal(value) for key, value in asdict(weights).items()
            },
        },
        "requested_top_n": requested_top_n,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return (
        f"{SCENARIO_SIGNATURE_PREFIX}:{sha256(canonical.encode('utf-8')).hexdigest()}"
    )


class OfflineStrategyReplayService:
    """Replay persisted ranking inputs without writes or external data access."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def replay_snapshot(
        self,
        snapshot_id: int,
        *,
        overrides: dict[str, Decimal] | None = None,
        top_n: int | None = None,
    ) -> SnapshotReplayResult:
        snapshot = self.session.scalar(
            select(StrategyReplaySnapshot)
            .where(StrategyReplaySnapshot.id == snapshot_id)
            .execution_options(autoflush=False)
        )
        if snapshot is None:
            return self._missing_snapshot(snapshot_id)
        candidates = tuple(
            self.session.scalars(
                select(StrategyReplayCandidate)
                .where(
                    StrategyReplayCandidate.strategy_replay_snapshot_id == snapshot.id
                )
                .order_by(
                    StrategyReplayCandidate.prefilter_rank.asc().nulls_last(),
                    StrategyReplayCandidate.market,
                )
                .execution_options(autoflush=False)
            )
        )
        return self._replay_loaded(
            snapshot, candidates, overrides=overrides, top_n=top_n
        )

    def replay_latest(
        self,
        limit: int,
        *,
        overrides: dict[str, Decimal] | None = None,
        top_n: int | None = None,
    ) -> BatchReplayResult:
        if limit < 1:
            raise ReplayInputError("latest snapshot count must be >= 1")
        snapshots = tuple(
            self.session.scalars(
                select(StrategyReplaySnapshot)
                .order_by(
                    StrategyReplaySnapshot.captured_at.desc(),
                    StrategyReplaySnapshot.id.desc(),
                )
                .limit(limit)
                .execution_options(autoflush=False)
            )
        )
        if not snapshots:
            return self._summarize(limit, ())
        snapshot_ids = [snapshot.id for snapshot in snapshots]
        candidates_by_snapshot: dict[int, list[StrategyReplayCandidate]] = {
            snapshot_id: [] for snapshot_id in snapshot_ids
        }
        for candidate in self.session.scalars(
            select(StrategyReplayCandidate)
            .where(
                StrategyReplayCandidate.strategy_replay_snapshot_id.in_(snapshot_ids)
            )
            .order_by(
                StrategyReplayCandidate.strategy_replay_snapshot_id,
                StrategyReplayCandidate.prefilter_rank.asc().nulls_last(),
                StrategyReplayCandidate.market,
            )
            .execution_options(autoflush=False)
        ):
            candidates_by_snapshot[candidate.strategy_replay_snapshot_id].append(
                candidate
            )
        results = tuple(
            self._replay_loaded(
                snapshot,
                candidates_by_snapshot[snapshot.id],
                overrides=overrides,
                top_n=top_n,
            )
            for snapshot in snapshots
        )
        return self._summarize(limit, results)

    def replay_snapshots(
        self,
        snapshot_ids: Iterable[int],
        *,
        overrides: dict[str, Decimal] | None = None,
        top_n: int | None = None,
    ) -> BatchReplayResult:
        """Replay an explicit ordered snapshot subset with batched DB reads."""
        ids = tuple(snapshot_ids)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in ids
        ):
            raise ReplayInputError("snapshot IDs must be positive integers")
        if len(ids) != len(set(ids)):
            raise ReplayInputError("snapshot IDs must be unique")
        if not ids:
            return self._summarize(0, ())
        snapshots_by_id = {
            snapshot.id: snapshot
            for snapshot in self.session.scalars(
                select(StrategyReplaySnapshot)
                .where(StrategyReplaySnapshot.id.in_(ids))
                .execution_options(autoflush=False)
            )
        }
        candidates_by_snapshot: dict[int, list[StrategyReplayCandidate]] = {
            snapshot_id: [] for snapshot_id in snapshots_by_id
        }
        for candidate in self.session.scalars(
            select(StrategyReplayCandidate)
            .where(StrategyReplayCandidate.strategy_replay_snapshot_id.in_(ids))
            .order_by(
                StrategyReplayCandidate.strategy_replay_snapshot_id,
                StrategyReplayCandidate.prefilter_rank.asc().nulls_last(),
                StrategyReplayCandidate.market,
            )
            .execution_options(autoflush=False)
        ):
            candidates_by_snapshot.setdefault(
                candidate.strategy_replay_snapshot_id, []
            ).append(candidate)
        results = tuple(
            self._missing_snapshot(snapshot_id)
            if (snapshot := snapshots_by_id.get(snapshot_id)) is None
            else self._replay_loaded(
                snapshot,
                candidates_by_snapshot[snapshot_id],
                overrides=overrides,
                top_n=top_n,
            )
            for snapshot_id in ids
        )
        return self._summarize(len(ids), results)

    def summarize_results(
        self, requested_count: int, results: Iterable[SnapshotReplayResult]
    ) -> BatchReplayResult:
        return self._summarize(requested_count, tuple(results))

    def _replay_loaded(
        self,
        snapshot: StrategyReplaySnapshot,
        candidates: Iterable[StrategyReplayCandidate],
        *,
        overrides: dict[str, Decimal] | None,
        top_n: int | None,
    ) -> SnapshotReplayResult:
        if snapshot.dataset_schema_version != DATASET_SCHEMA_VERSION:
            return self._incompatible(
                snapshot,
                "UNSUPPORTED_DATASET_SCHEMA",
                f"unsupported dataset schema: {snapshot.dataset_schema_version}",
            )
        try:
            if (
                not isinstance(snapshot.policy_data, dict)
                or snapshot.policy_data.get("dataset_schema_version")
                != snapshot.dataset_schema_version
            ):
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    "snapshot and policy_data dataset schema versions do not match",
                )
            if policy_signature(snapshot.policy_data) != snapshot.policy_signature:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    "stored policy signature does not match policy_data",
                )
            baseline_weights, stored_top_n = restore_weights(snapshot.policy_data)
            requested_top_n = stored_top_n if top_n is None else top_n
            if isinstance(requested_top_n, bool) or requested_top_n < 1:
                raise ReplayInputError("top_n must be >= 1")
            scenario_weights = apply_overrides(baseline_weights, overrides)
            rows = tuple(candidates)
            if not rows:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA", "snapshot has no replay candidates"
                )
            self._validate_snapshot_counts(snapshot, rows)
            rankable_rows, reconstructed = self._reconstruct_rankable(rows)
            baseline_ranked = HeuristicMarketRankingPolicy(baseline_weights).rank(
                reconstructed
            )
            scenario_ranked = HeuristicMarketRankingPolicy(scenario_weights).rank(
                reconstructed
            )
            return self._build_success_result(
                snapshot=snapshot,
                rows=rows,
                rankable_rows=rankable_rows,
                baseline_ranked=baseline_ranked,
                scenario_ranked=scenario_ranked,
                stored_top_n=stored_top_n,
                requested_top_n=requested_top_n,
                scenario_weights=scenario_weights,
            )
        except _IncompatibleSnapshotError as error:
            return self._incompatible(snapshot, error.status, error.reason)

    @staticmethod
    def _validate_snapshot_counts(
        snapshot: StrategyReplaySnapshot,
        rows: tuple[StrategyReplayCandidate, ...],
    ) -> None:
        actual = {
            "research_candidate_count": len(rows),
            "prefilter_candidate_count": sum(row.in_prefilter for row in rows),
            "ranked_candidate_count": sum(
                row.original_rank is not None for row in rows
            ),
            "final_candidate_count": sum(row.final_selected for row in rows),
        }
        for field_name, actual_count in actual.items():
            if getattr(snapshot, field_name, None) != actual_count:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"stored {field_name} does not match candidate rows",
                )

    @staticmethod
    def _reconstruct_rankable(
        rows: tuple[StrategyReplayCandidate, ...],
    ) -> tuple[tuple[StrategyReplayCandidate, ...], list[dict[str, Any]]]:
        prefilter_ranks: set[int] = set()
        markets: set[str] = set()
        rankable_rows: list[StrategyReplayCandidate] = []
        reconstructed: list[dict[str, Any]] = []
        for row in rows:
            if not row.market or row.market in markets:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"candidate market missing or duplicate: market={row.market}",
                )
            markets.add(row.market)
            feature_data = row.feature_data
            if not isinstance(feature_data, dict):
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA", f"feature_data missing: market={row.market}"
                )
            if "enough_candles" not in feature_data or not isinstance(
                feature_data["enough_candles"], bool
            ):
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"enough_candles missing or invalid: market={row.market}",
                )
            if row.in_prefilter:
                if (
                    isinstance(row.prefilter_rank, bool)
                    or not isinstance(row.prefilter_rank, int)
                    or row.prefilter_rank < 1
                    or row.prefilter_rank in prefilter_ranks
                ):
                    raise _IncompatibleSnapshotError(
                        "INVALID_REPLAY_DATA",
                        f"prefilter rank missing or duplicate: market={row.market}",
                    )
                prefilter_ranks.add(row.prefilter_rank)
            elif row.prefilter_rank is not None:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"non-prefilter candidate has prefilter rank: market={row.market}",
                )
            is_rankable = (
                row.in_prefilter
                and row.buy_eligible
                and feature_data["enough_candles"] is True
            )
            if not is_rankable:
                if row.original_rank is not None or row.original_score is not None:
                    raise _IncompatibleSnapshotError(
                        "INVALID_REPLAY_DATA",
                        f"non-rankable candidate has original ranking: market={row.market}",
                    )
                continue
            if row.original_rank is None or row.original_score is None:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"rankable candidate lacks original ranking: market={row.market}",
                )
            if (
                isinstance(row.original_rank, bool)
                or not isinstance(row.original_rank, int)
                or row.original_rank < 1
            ):
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"original rank is invalid: market={row.market}",
                )
            try:
                _decimal(row.original_score, field_name=f"{row.market}.original_score")
            except ReplayInputError as error:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA", str(error)
                ) from error
            market = feature_data.get("market")
            timeframes = feature_data.get("timeframes")
            orderbook = feature_data.get("orderbook")
            raw_liquidity = feature_data.get("quote_trade_value_24h")
            if market != row.market:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"feature market mismatch: market={row.market}",
                )
            if not isinstance(timeframes, dict) or not isinstance(orderbook, dict):
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"ranking feature structure invalid: market={row.market}",
                )
            try:
                liquidity = _decimal(
                    raw_liquidity, field_name=f"{row.market}.quote_trade_value_24h"
                )
            except ReplayInputError as error:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA", str(error)
                ) from error
            if liquidity < 0:
                raise _IncompatibleSnapshotError(
                    "INVALID_REPLAY_DATA",
                    f"quote_trade_value_24h must be >= 0: market={row.market}",
                )
            rankable_rows.append(row)
            reconstructed.append(
                {
                    **feature_data,
                    "market": row.market,
                    "quote_trade_value_24h": liquidity,
                    "timeframes": timeframes,
                    "orderbook": orderbook,
                }
            )
        expected_ranks = set(range(1, len(rankable_rows) + 1))
        stored_ranks = {row.original_rank for row in rankable_rows}
        if stored_ranks != expected_ranks:
            raise _IncompatibleSnapshotError(
                "INVALID_REPLAY_DATA",
                "stored original ranks must be unique and contiguous for rankable candidates",
            )
        return tuple(rankable_rows), reconstructed

    @staticmethod
    def _build_success_result(
        *,
        snapshot: StrategyReplaySnapshot,
        rows: tuple[StrategyReplayCandidate, ...],
        rankable_rows: tuple[StrategyReplayCandidate, ...],
        baseline_ranked: list[dict[str, Any]],
        scenario_ranked: list[dict[str, Any]],
        stored_top_n: int,
        requested_top_n: int,
        scenario_weights: HeuristicRankingWeights,
    ) -> SnapshotReplayResult:
        stored_by_market = {row.market: row for row in rankable_rows}
        baseline_by_market = {
            candidate["market"]: (rank, candidate["score"])
            for rank, candidate in enumerate(baseline_ranked, start=1)
        }
        scenario_by_market = {
            candidate["market"]: (rank, candidate["score"])
            for rank, candidate in enumerate(scenario_ranked, start=1)
        }
        mismatches: list[BaselineMismatchDiagnostic] = []
        for market, row in stored_by_market.items():
            baseline_rank, baseline_score = baseline_by_market[market]
            original_score = Decimal(row.original_score)
            score_delta = baseline_score - original_score
            if baseline_rank != row.original_rank or abs(score_delta) > SCORE_TOLERANCE:
                mismatches.append(
                    BaselineMismatchDiagnostic(
                        market=market,
                        stored_original_rank=row.original_rank,
                        replay_baseline_rank=baseline_rank,
                        stored_original_score=original_score,
                        replay_baseline_score=baseline_score,
                        score_delta=score_delta,
                    )
                )
        baseline_top = tuple(
            row.market
            for row in sorted(rankable_rows, key=lambda item: item.original_rank)
            if row.original_rank <= stored_top_n
        )
        effective_top_n = min(requested_top_n, len(scenario_ranked))
        scenario_top = tuple(
            candidate["market"] for candidate in scenario_ranked[:effective_top_n]
        )
        baseline_set = set(baseline_top)
        scenario_set = set(scenario_top)
        overlap = baseline_set & scenario_set
        overlap_denominator = max(len(baseline_set), len(scenario_set))
        overlap_rate = (
            Decimal(len(overlap)) / Decimal(overlap_denominator)
            if overlap_denominator
            else Decimal("1")
        )
        candidate_results = tuple(
            CandidateReplayResult(
                market=market,
                original_rank=row.original_rank,
                baseline_replay_rank=baseline_by_market[market][0],
                scenario_rank=scenario_by_market[market][0],
                original_score=Decimal(row.original_score),
                baseline_replay_score=baseline_by_market[market][1],
                scenario_score=scenario_by_market[market][1],
                rank_delta_vs_original=(
                    scenario_by_market[market][0] - row.original_rank
                ),
                score_delta_vs_original=(
                    scenario_by_market[market][1] - Decimal(row.original_score)
                ),
                original_final_selected=row.final_selected,
                original_ranked_selected=market in baseline_set,
                scenario_selected=market in scenario_set,
                entered_top_n=market in scenario_set and market not in baseline_set,
                exited_top_n=market in baseline_set and market not in scenario_set,
            )
            for market, row in sorted(
                stored_by_market.items(), key=lambda item: item[1].original_rank
            )
        )
        return SnapshotReplayResult(
            snapshot_id=snapshot.id,
            pipeline_run_id=snapshot.pipeline_run_id,
            captured_at=snapshot.captured_at,
            dataset_schema_version=snapshot.dataset_schema_version,
            baseline_policy_signature=snapshot.policy_signature,
            scenario_signature=scenario_signature(scenario_weights, requested_top_n),
            status="BASELINE_MISMATCH" if mismatches else "SUCCESS",
            safe_reason="stored ranking was not reproduced" if mismatches else None,
            rankable_candidate_count=len(rankable_rows),
            stored_top_n=stored_top_n,
            requested_top_n=requested_top_n,
            effective_top_n=effective_top_n,
            held_augmented_count=sum(
                1
                for row in rows
                if row.final_selected and row.selection_source == "HELD"
            ),
            baseline_matches_stored=not mismatches,
            baseline_top_markets=baseline_top,
            scenario_top_markets=scenario_top,
            top_n_overlap_count=len(overlap),
            top_n_overlap_rate=overlap_rate,
            entered_top_n=tuple(sorted(scenario_set - baseline_set)),
            exited_top_n=tuple(sorted(baseline_set - scenario_set)),
            candidate_results=candidate_results,
            mismatch_diagnostics=tuple(mismatches),
        )

    @staticmethod
    def _incompatible(
        snapshot: StrategyReplaySnapshot, status: str, reason: str
    ) -> SnapshotReplayResult:
        return SnapshotReplayResult(
            snapshot_id=snapshot.id,
            pipeline_run_id=snapshot.pipeline_run_id,
            captured_at=snapshot.captured_at,
            dataset_schema_version=snapshot.dataset_schema_version,
            baseline_policy_signature=snapshot.policy_signature,
            scenario_signature=None,
            status=status,
            safe_reason=reason,
            rankable_candidate_count=0,
            stored_top_n=None,
            requested_top_n=None,
            effective_top_n=0,
            held_augmented_count=0,
            baseline_matches_stored=False,
            baseline_top_markets=(),
            scenario_top_markets=(),
            top_n_overlap_count=0,
            top_n_overlap_rate=Decimal("0"),
            entered_top_n=(),
            exited_top_n=(),
            candidate_results=(),
            mismatch_diagnostics=(),
        )

    @staticmethod
    def _missing_snapshot(snapshot_id: int) -> SnapshotReplayResult:
        return SnapshotReplayResult(
            snapshot_id=snapshot_id,
            pipeline_run_id=None,
            captured_at=None,
            dataset_schema_version=None,
            baseline_policy_signature=None,
            scenario_signature=None,
            status="INVALID_REPLAY_DATA",
            safe_reason="snapshot not found",
            rankable_candidate_count=0,
            stored_top_n=None,
            requested_top_n=None,
            effective_top_n=0,
            held_augmented_count=0,
            baseline_matches_stored=False,
            baseline_top_markets=(),
            scenario_top_markets=(),
            top_n_overlap_count=0,
            top_n_overlap_rate=Decimal("0"),
            entered_top_n=(),
            exited_top_n=(),
            candidate_results=(),
            mismatch_diagnostics=(),
        )

    @staticmethod
    def _summarize(
        requested_count: int, results: tuple[SnapshotReplayResult, ...]
    ) -> BatchReplayResult:
        compatible = tuple(result for result in results if result.compatible)
        rank_changes = [
            abs(candidate.rank_delta_vs_original)
            for result in compatible
            for candidate in result.candidate_results
        ]
        mean_overlap = (
            sum(
                (result.top_n_overlap_rate for result in compatible),
                start=Decimal("0"),
            )
            / Decimal(len(compatible))
            if compatible
            else Decimal("0")
        )
        mean_rank_change = (
            Decimal(sum(rank_changes)) / Decimal(len(rank_changes))
            if rank_changes
            else Decimal("0")
        )
        return BatchReplayResult(
            requested_snapshot_count=requested_count,
            replayed_snapshot_count=len(results),
            compatible_snapshot_count=len(compatible),
            incompatible_snapshot_count=len(results) - len(compatible),
            baseline_match_count=sum(
                result.status == "SUCCESS" for result in compatible
            ),
            baseline_mismatch_count=sum(
                result.status == "BASELINE_MISMATCH" for result in compatible
            ),
            mean_top_n_overlap_rate=mean_overlap,
            mean_absolute_rank_change=mean_rank_change,
            total_entered_top_n=sum(len(result.entered_top_n) for result in compatible),
            total_exited_top_n=sum(len(result.exited_top_n) for result in compatible),
            results=results,
        )
