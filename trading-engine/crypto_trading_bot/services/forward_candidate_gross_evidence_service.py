from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import ResearchPolicyCandidate, StrategyReplaySnapshot
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    InvalidForwardCandidateProvenance,
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
    restore_weights,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    INVALID_OUTCOME_DATA,
    OUTCOME_INCOMPLETE,
    REPLAY_INCOMPATIBLE,
    RESULT_TYPE as GROSS_PERFORMANCE_METRIC_TYPE,
    SUCCESS as AB_SUCCESS,
    StrategyABBatchPerformanceResult,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import policy_signature


RESULT_TYPE = "FORWARD_ONLY_RANKING_SELECTION_GROSS_EVIDENCE"
SUCCESS = "SUCCESS"
NO_FORWARD_SNAPSHOTS = "NO_FORWARD_SNAPSHOTS"
FORWARD_OUTCOMES_PENDING = "FORWARD_OUTCOMES_PENDING"
NO_COMPARABLE_FORWARD_SNAPSHOTS = "NO_COMPARABLE_FORWARD_SNAPSHOTS"
INVALID_FORWARD_EVIDENCE = "INVALID_FORWARD_EVIDENCE"
_KNOWN_AB_STATUSES = frozenset(
    {
        AB_SUCCESS,
        OUTCOME_INCOMPLETE,
        BASELINE_INTEGRITY_FAILED,
        REPLAY_INCOMPATIBLE,
        INVALID_OUTCOME_DATA,
    }
)


class _InvalidForwardEvidence(Exception):
    pass


@dataclass(frozen=True)
class ForwardCandidateHorizonEvidence:
    horizon_minutes: int
    eligible_forward_snapshot_count: int
    successful_comparable_snapshot_count: int
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
    status: str
    safe_reason: str | None
    snapshots: tuple[StrategyABSnapshotPerformanceResult, ...]


@dataclass(frozen=True)
class ForwardCandidateGrossEvidenceResult:
    candidate_id: int
    candidate: ForwardCandidateMetadata | None
    requested_horizons: tuple[int, ...]
    eligible_forward_snapshot_ids: tuple[int, ...]
    status: str
    safe_reason: str | None
    candidate_registration_verified: bool
    forward_anchor_enforced: bool
    pre_registration_snapshots_excluded: bool
    registration_time_provenance_verified: bool
    future_snapshot_cutoff_verified: bool
    scenario_definition_frozen_at_registration: bool
    forward_evidence_generated: bool
    forward_validation_performed: bool
    horizons: tuple[ForwardCandidateHorizonEvidence, ...]


class ForwardCandidateGrossEvidenceService:
    """Evaluate immutable candidates on strictly post-registration DB evidence."""

    def __init__(
        self,
        session: Session,
        *,
        performance_service: StrategyABPerformanceService | None = None,
    ) -> None:
        self.session = session
        self.performance_service = performance_service or StrategyABPerformanceService(
            session
        )

    def evaluate(
        self,
        *,
        candidate_id: int,
        horizons: Iterable[int],
        snapshot_id_ceiling: int | None = None,
    ) -> ForwardCandidateGrossEvidenceResult:
        normalized_horizons = self._normalize_horizons(horizons)
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        try:
            validated = load_and_validate_forward_candidate(self.session, candidate_id)
            candidate = validated.row
            self._validate_ceiling(
                snapshot_id_ceiling, candidate.registration_snapshot_id_watermark
            )
            metadata = validated.metadata
            overrides = dict(validated.scenario.component_weights)
            snapshots = self._load_forward_snapshots(candidate, snapshot_id_ceiling)
            self._validate_forward_snapshots(candidate, snapshots)
            evidence = tuple(
                self._evaluate_horizon(
                    candidate,
                    snapshots,
                    horizon=horizon,
                    overrides=overrides,
                )
                for horizon in normalized_horizons
            )
            status, safe_reason = self._overall_status(snapshots, evidence)
            return ForwardCandidateGrossEvidenceResult(
                candidate_id=candidate_id,
                candidate=metadata,
                requested_horizons=normalized_horizons,
                eligible_forward_snapshot_ids=tuple(item.id for item in snapshots),
                status=status,
                safe_reason=safe_reason,
                candidate_registration_verified=True,
                forward_anchor_enforced=True,
                pre_registration_snapshots_excluded=True,
                registration_time_provenance_verified=True,
                future_snapshot_cutoff_verified=True,
                scenario_definition_frozen_at_registration=True,
                forward_evidence_generated=bool(snapshots),
                forward_validation_performed=True,
                horizons=evidence,
            )
        except (_InvalidForwardEvidence, InvalidForwardCandidateProvenance) as error:
            return self._invalid(candidate_id, normalized_horizons, str(error))

    def _load_forward_snapshots(
        self,
        candidate: ResearchPolicyCandidate,
        snapshot_id_ceiling: int | None = None,
    ) -> tuple[StrategyReplaySnapshot, ...]:
        query = select(StrategyReplaySnapshot).where(
            StrategyReplaySnapshot.user_id == candidate.user_id,
            StrategyReplaySnapshot.exchange == candidate.exchange,
            StrategyReplaySnapshot.quote_asset == candidate.quote_asset,
            StrategyReplaySnapshot.dataset_schema_version
            == candidate.dataset_schema_version,
            StrategyReplaySnapshot.policy_signature
            == candidate.baseline_policy_signature,
            StrategyReplaySnapshot.id > candidate.registration_snapshot_id_watermark,
            StrategyReplaySnapshot.captured_at > candidate.registered_at,
            StrategyReplaySnapshot.captured_at
            > candidate.registration_captured_at_watermark,
        )
        if snapshot_id_ceiling is not None:
            query = query.where(StrategyReplaySnapshot.id <= snapshot_id_ceiling)
        return tuple(
            self.session.scalars(
                query.order_by(
                    StrategyReplaySnapshot.captured_at.asc(),
                    StrategyReplaySnapshot.id.asc(),
                ).execution_options(autoflush=False)
            )
        )

    @staticmethod
    def _validate_ceiling(value: int | None, watermark: int) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < watermark:
            raise ReplayInputError(
                "snapshot ID ceiling must be an integer at or above registration watermark"
            )

    def _validate_forward_snapshots(
        self,
        candidate: ResearchPolicyCandidate,
        snapshots: tuple[StrategyReplaySnapshot, ...],
    ) -> None:
        registered_at = self._aware_utc(candidate.registered_at, "registered_at")
        watermark_at = self._aware_utc(
            candidate.registration_captured_at_watermark,
            "registration_captured_at_watermark",
        )
        seen_ids: set[int] = set()
        previous_key: tuple[datetime, int] | None = None
        for snapshot in snapshots:
            if snapshot.id in seen_ids:
                raise _InvalidForwardEvidence("duplicate forward snapshot ID")
            seen_ids.add(snapshot.id)
            captured_at = self._aware_utc(snapshot.captured_at, "snapshot captured_at")
            chronology_key = (captured_at, snapshot.id)
            if previous_key is not None and chronology_key <= previous_key:
                raise _InvalidForwardEvidence("forward snapshot chronology is invalid")
            previous_key = chronology_key
            if (
                snapshot.id <= candidate.registration_snapshot_id_watermark
                or captured_at <= registered_at
                or captured_at <= watermark_at
            ):
                raise _InvalidForwardEvidence("forward snapshot violates triple cutoff")
            if (
                snapshot.user_id != candidate.user_id
                or snapshot.exchange != candidate.exchange
                or snapshot.quote_asset != candidate.quote_asset
                or snapshot.dataset_schema_version != candidate.dataset_schema_version
                or snapshot.policy_signature != candidate.baseline_policy_signature
            ):
                raise _InvalidForwardEvidence("forward snapshot context mismatch")
            if not isinstance(snapshot.policy_data, dict):
                raise _InvalidForwardEvidence("forward snapshot policy_data is invalid")
            if policy_signature(snapshot.policy_data) != snapshot.policy_signature:
                raise _InvalidForwardEvidence(
                    "forward snapshot baseline policy signature mismatch"
                )
            try:
                _, stored_top_n = restore_weights(snapshot.policy_data)
            except Exception as error:
                raise _InvalidForwardEvidence(
                    f"forward snapshot ranking policy is invalid: {error}"
                ) from error
            if stored_top_n != candidate.effective_top_n:
                raise _InvalidForwardEvidence(
                    "forward snapshot effective TopN mismatch"
                )

    def _evaluate_horizon(
        self,
        candidate: ResearchPolicyCandidate,
        snapshots: tuple[StrategyReplaySnapshot, ...],
        *,
        horizon: int,
        overrides: dict[str, Decimal],
    ) -> ForwardCandidateHorizonEvidence:
        results = (
            self.performance_service.evaluate_snapshots(
                tuple(snapshot.id for snapshot in snapshots),
                horizon_minutes=horizon,
                overrides=overrides,
            )
            if snapshots
            else ()
        )
        self._validate_ab_results(candidate, snapshots, horizon, results)
        aggregate = self.performance_service.summarize_results(len(results), results)
        status, safe_reason = self._horizon_status(len(snapshots), aggregate)
        return ForwardCandidateHorizonEvidence(
            horizon_minutes=horizon,
            eligible_forward_snapshot_count=len(snapshots),
            successful_comparable_snapshot_count=aggregate.successful_snapshot_count,
            outcome_incomplete_count=aggregate.outcome_incomplete_count,
            baseline_integrity_failed_count=aggregate.baseline_integrity_failed_count,
            replay_incompatible_count=aggregate.replay_incompatible_count,
            invalid_outcome_count=aggregate.invalid_outcome_count,
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
            status=status,
            safe_reason=safe_reason,
            snapshots=results,
        )

    def _validate_ab_results(
        self,
        candidate: ResearchPolicyCandidate,
        snapshots: tuple[StrategyReplaySnapshot, ...],
        horizon: int,
        results: tuple[StrategyABSnapshotPerformanceResult, ...],
    ) -> None:
        if len(results) != len(snapshots):
            raise _InvalidForwardEvidence("A/B result snapshot count mismatch")
        for snapshot, result in zip(snapshots, results, strict=True):
            if (
                result.snapshot_id != snapshot.id
                or result.pipeline_run_id != snapshot.pipeline_run_id
                or result.horizon_minutes != horizon
                or result.baseline_policy_signature
                != candidate.baseline_policy_signature
                or result.status not in _KNOWN_AB_STATUSES
            ):
                raise _InvalidForwardEvidence("A/B result lineage mismatch")
            result_at = self._aware_utc(result.captured_at, "A/B result captured_at")
            snapshot_at = self._aware_utc(snapshot.captured_at, "snapshot captured_at")
            if result_at != snapshot_at:
                raise _InvalidForwardEvidence("A/B result captured_at mismatch")
            if (result.status == AB_SUCCESS) is not result.performance_evaluated:
                raise _InvalidForwardEvidence("A/B performance status is inconsistent")
            if result.status == AB_SUCCESS and (
                result.effective_top_n != candidate.effective_top_n
                or result.scenario_signature is None
            ):
                raise _InvalidForwardEvidence("successful A/B result context mismatch")

    @staticmethod
    def _horizon_status(
        eligible_count: int, aggregate: StrategyABBatchPerformanceResult
    ) -> tuple[str, str | None]:
        if eligible_count == 0:
            return NO_FORWARD_SNAPSHOTS, "no snapshot satisfies the triple cutoff"
        if aggregate.successful_snapshot_count:
            return SUCCESS, None
        if aggregate.outcome_incomplete_count == eligible_count:
            return (
                FORWARD_OUTCOMES_PENDING,
                "all eligible forward snapshot outcomes are incomplete",
            )
        return (
            NO_COMPARABLE_FORWARD_SNAPSHOTS,
            "eligible forward snapshots have no comparable successful result",
        )

    @staticmethod
    def _overall_status(
        snapshots: tuple[StrategyReplaySnapshot, ...],
        horizons: tuple[ForwardCandidateHorizonEvidence, ...],
    ) -> tuple[str, str | None]:
        if not snapshots:
            return NO_FORWARD_SNAPSHOTS, "no snapshot satisfies the triple cutoff"
        if any(item.status == SUCCESS for item in horizons):
            return SUCCESS, None
        if all(item.status == FORWARD_OUTCOMES_PENDING for item in horizons):
            return (
                FORWARD_OUTCOMES_PENDING,
                "all requested horizons are awaiting complete outcomes",
            )
        return (
            NO_COMPARABLE_FORWARD_SNAPSHOTS,
            "no requested horizon has a comparable successful result",
        )

    @staticmethod
    def _normalize_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
        values = tuple(horizons)
        if not values or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in values
        ):
            raise ReplayInputError("horizons must contain positive integers")
        return tuple(sorted(set(values)))

    @staticmethod
    def _aware_utc(value: object, field_name: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise _InvalidForwardEvidence(f"{field_name} must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _invalid(
        candidate_id: int, horizons: tuple[int, ...], reason: str
    ) -> ForwardCandidateGrossEvidenceResult:
        return ForwardCandidateGrossEvidenceResult(
            candidate_id=candidate_id,
            candidate=None,
            requested_horizons=horizons,
            eligible_forward_snapshot_ids=(),
            status=INVALID_FORWARD_EVIDENCE,
            safe_reason=reason,
            candidate_registration_verified=False,
            forward_anchor_enforced=False,
            pre_registration_snapshots_excluded=False,
            registration_time_provenance_verified=False,
            future_snapshot_cutoff_verified=False,
            scenario_definition_frozen_at_registration=False,
            forward_evidence_generated=False,
            forward_validation_performed=True,
            horizons=(),
        )


__all__ = [
    "FORWARD_OUTCOMES_PENDING",
    "GROSS_PERFORMANCE_METRIC_TYPE",
    "INVALID_FORWARD_EVIDENCE",
    "NO_COMPARABLE_FORWARD_SNAPSHOTS",
    "NO_FORWARD_SNAPSHOTS",
    "RESULT_TYPE",
    "SUCCESS",
    "ForwardCandidateGrossEvidenceResult",
    "ForwardCandidateGrossEvidenceService",
    "ForwardCandidateHorizonEvidence",
    "ForwardCandidateMetadata",
]
