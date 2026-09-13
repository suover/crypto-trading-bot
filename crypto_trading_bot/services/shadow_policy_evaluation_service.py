from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ShadowPolicyEvaluation,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    InvalidForwardCandidateProvenance,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
    SnapshotReplayResult,
    OfflineStrategyReplayService,
    restore_weights,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentError,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    ValidatedShadowPolicyEnrollment,
    load_and_validate_shadow_policy_enrollment,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    policy_signature,
)


EVALUATION_SCHEMA_VERSION = "shadow-policy-evaluation-v1"
EVALUATION_SIGNATURE_PREFIX = "shadow-selection-evaluation-v1"
SUCCESS = "SUCCESS"
CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
BASELINE_INTEGRITY_FAILED = "BASELINE_INTEGRITY_FAILED"
REPLAY_INCOMPATIBLE = "REPLAY_INCOMPATIBLE"
DRY_RUN = "DRY_RUN"
NO_SHADOW_ENROLLMENT = "NO_SHADOW_ENROLLMENT"
NO_POST_ENROLLMENT_SNAPSHOTS = "NO_POST_ENROLLMENT_SNAPSHOTS"
NO_NEW_SHADOW_EVALUATIONS = "NO_NEW_SHADOW_EVALUATIONS"
INVALID_SHADOW_EVALUATION = "INVALID_SHADOW_EVALUATION"
NOT_REPLAYED = "NOT_REPLAYED"


class ShadowPolicyEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class ShadowPolicyEvaluationResult:
    candidate_id: int
    enrollment: object | None
    evaluation_schema_version: str
    evaluation_snapshot_id_ceiling: int | None
    timeline_snapshot_ids: tuple[int, ...]
    candidate_context_snapshot_ids: tuple[int, ...]
    existing_evaluation_count: int
    planned_evaluation_count: int
    created_evaluation_count: int
    success_count: int
    context_mismatch_count: int
    baseline_integrity_failed_count: int
    replay_incompatible_count: int
    status: str
    safe_reason: str | None
    evaluations: tuple[ShadowPolicyEvaluation, ...]
    preview_only: bool
    database_write: bool
    outcome_data_used: bool = False
    performance_evaluated: bool = False
    policy_decision_performed: bool = False
    shadow_runtime_enabled: bool = False
    promotion_performed: bool = False
    external_calls: bool = False
    live_policy_change: bool = False


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ShadowPolicyEvaluationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_string(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ShadowPolicyEvaluationError("signature Decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonicalize(value):
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def _evaluation_payload(row) -> dict:
    fields = (
        "evaluation_schema_version",
        "shadow_enrollment_id",
        "candidate_id",
        "gate_decision_signature",
        "shadow_enrolled_at",
        "shadow_snapshot_id_watermark",
        "shadow_captured_at_watermark",
        "strategy_replay_snapshot_id",
        "pipeline_run_id",
        "snapshot_captured_at",
        "dataset_schema_version",
        "snapshot_policy_signature",
        "snapshot_stored_top_n",
        "baseline_policy_signature",
        "effective_top_n",
        "scenario_name",
        "scenario_definition_signature",
        "scenario_signature",
        "context_matches_enrollment",
        "replay_status",
        "baseline_matches_stored",
        "baseline_top_markets",
        "shadow_top_markets",
        "top_n_overlap_count",
        "top_n_overlap_rate",
        "entered_top_n",
        "exited_top_n",
        "rankable_candidate_count",
        "evaluation_status",
        "safe_reason",
        "evaluated_at",
    )
    return {field: getattr(row, field) for field in fields}


def evaluation_signature(row) -> str:
    canonical = json.dumps(
        _canonicalize(_evaluation_payload(row)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return (
        f"{EVALUATION_SIGNATURE_PREFIX}:{sha256(canonical.encode('utf-8')).hexdigest()}"
    )


class ShadowPolicyEvaluationService:
    def __init__(
        self,
        session: Session,
        *,
        replay_service=None,
        now_fn: Callable[[], datetime] | None = None,
        after_ceiling_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.replay_service = replay_service or OfflineStrategyReplayService(session)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.after_ceiling_fn = after_ceiling_fn

    def preview(self, *, candidate_id: int) -> ShadowPolicyEvaluationResult:
        return self._evaluate(candidate_id=candidate_id, apply=False)

    def evaluate(self, *, candidate_id: int) -> ShadowPolicyEvaluationResult:
        return self._evaluate(candidate_id=candidate_id, apply=True)

    def _evaluate(self, *, candidate_id: int, apply: bool):
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        enrollment = None
        try:
            validated = load_and_validate_shadow_policy_enrollment(
                self.session, candidate_id
            )
            if validated is None:
                return self._result(
                    candidate_id,
                    None,
                    NO_SHADOW_ENROLLMENT,
                    "candidate has no Shadow enrollment",
                    preview_only=not apply,
                )
            enrollment = validated.row
            evaluated_at = _utc(self.now_fn(), "shadow evaluation clock")
            ceiling = self._ceiling(validated)
            existing = self._existing(enrollment.id)
            if ceiling == enrollment.shadow_snapshot_id_watermark:
                if existing:
                    raise ShadowPolicyEvaluationError(
                        "stored evaluations exist outside the Shadow boundary"
                    )
                return self._result(
                    candidate_id,
                    enrollment,
                    NO_POST_ENROLLMENT_SNAPSHOTS,
                    "no genuine post-enrollment snapshots",
                    preview_only=not apply,
                    ceiling=ceiling,
                )
            if self.after_ceiling_fn is not None:
                self.after_ceiling_fn()
            snapshots = self._timeline(validated, ceiling)
            if not snapshots:
                raise ShadowPolicyEvaluationError(
                    "evaluation ceiling has no matching timeline snapshots"
                )
            snapshot_context = {
                snapshot.id: self._validate_snapshot(snapshot, validated)
                for snapshot in snapshots
            }
            if any(
                row.strategy_replay_snapshot_id not in snapshot_context
                for row in existing
            ):
                raise ShadowPolicyEvaluationError(
                    "stored evaluation is outside the genuine Shadow timeline"
                )
            existing_by_snapshot = {
                row.strategy_replay_snapshot_id: row for row in existing
            }
            for row in existing:
                snapshot = next(
                    item
                    for item in snapshots
                    if item.id == row.strategy_replay_snapshot_id
                )
                self._validate_existing(
                    row, snapshot, snapshot_context[snapshot.id], validated
                )
            pending = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.id not in existing_by_snapshot
            )
            if not pending:
                return self._result(
                    candidate_id,
                    enrollment,
                    NO_NEW_SHADOW_EVALUATIONS,
                    "all snapshots within the invocation ceiling are already evaluated",
                    preview_only=not apply,
                    ceiling=ceiling,
                    snapshots=snapshots,
                    existing=existing,
                    contexts=snapshot_context,
                )
            candidate_pending = tuple(
                snapshot.id for snapshot in pending if snapshot_context[snapshot.id][0]
            )
            replay_results = {}
            if candidate_pending:
                batch = self.replay_service.replay_snapshots(
                    candidate_pending,
                    overrides=validated.scenario.component_weights,
                    top_n=None,
                )
                if (
                    batch.requested_snapshot_count != len(candidate_pending)
                    or tuple(item.snapshot_id for item in batch.results)
                    != candidate_pending
                ):
                    raise ShadowPolicyEvaluationError(
                        "Offline Replay result set does not match requested snapshots"
                    )
                replay_results = {item.snapshot_id: item for item in batch.results}
            plans = tuple(
                self._plan(
                    snapshot,
                    snapshot_context[snapshot.id],
                    replay_results.get(snapshot.id),
                    validated,
                    evaluated_at,
                )
                for snapshot in pending
            )
            for plan in plans:
                self._validate_evaluation(plan, validated)
            if not apply:
                return self._result(
                    candidate_id,
                    enrollment,
                    DRY_RUN,
                    None,
                    preview_only=True,
                    ceiling=ceiling,
                    snapshots=snapshots,
                    existing=existing,
                    plans=plans,
                    contexts=snapshot_context,
                )
            try:
                with self.session.begin_nested():
                    self.session.add_all(plans)
                    self.session.flush()
            except IntegrityError:
                concurrent = self._existing(enrollment.id)
                concurrent_by_snapshot = {
                    row.strategy_replay_snapshot_id: row for row in concurrent
                }
                if any(
                    snapshot.id not in concurrent_by_snapshot for snapshot in pending
                ):
                    raise ShadowPolicyEvaluationError(
                        "concurrent evaluation only persisted part of the batch"
                    ) from None
                for snapshot in snapshots:
                    self._validate_existing(
                        concurrent_by_snapshot[snapshot.id],
                        snapshot,
                        snapshot_context[snapshot.id],
                        validated,
                    )
                return self._result(
                    candidate_id,
                    enrollment,
                    NO_NEW_SHADOW_EVALUATIONS,
                    "a concurrent invocation persisted the immutable evaluations",
                    preview_only=False,
                    ceiling=ceiling,
                    snapshots=snapshots,
                    existing=concurrent,
                    contexts=snapshot_context,
                )
            return self._result(
                candidate_id,
                enrollment,
                SUCCESS,
                None,
                preview_only=False,
                ceiling=ceiling,
                snapshots=snapshots,
                existing=existing,
                plans=plans,
                created_count=len(plans),
                contexts=snapshot_context,
            )
        except (
            InvalidForwardCandidateProvenance,
            ShadowPolicyEnrollmentError,
            ShadowPolicyEvaluationError,
            ReplayInputError,
            AttributeError,
            TypeError,
        ) as error:
            return self._result(
                candidate_id,
                enrollment,
                INVALID_SHADOW_EVALUATION,
                str(error),
                preview_only=not apply,
            )

    def _ceiling(self, validated: ValidatedShadowPolicyEnrollment) -> int:
        row = validated.row
        value = self.session.scalar(
            select(func.max(StrategyReplaySnapshot.id))
            .where(*self._boundary_predicates(validated))
            .execution_options(autoflush=False)
        )
        return row.shadow_snapshot_id_watermark if value is None else value

    def _timeline(self, validated, ceiling):
        snapshots = tuple(
            self.session.scalars(
                select(StrategyReplaySnapshot)
                .where(
                    *self._boundary_predicates(validated),
                    StrategyReplaySnapshot.id <= ceiling,
                )
                .order_by(
                    StrategyReplaySnapshot.captured_at,
                    StrategyReplaySnapshot.id,
                )
                .execution_options(autoflush=False)
            )
        )
        keys = tuple(
            (_utc(row.captured_at, "snapshot captured_at"), row.id) for row in snapshots
        )
        if len({row.id for row in snapshots}) != len(snapshots) or keys != tuple(
            sorted(keys)
        ):
            raise ShadowPolicyEvaluationError("snapshot chronology is invalid")
        return snapshots

    @staticmethod
    def _boundary_predicates(validated):
        row = validated.row
        return (
            StrategyReplaySnapshot.user_id == row.user_id,
            StrategyReplaySnapshot.exchange == row.exchange,
            StrategyReplaySnapshot.quote_asset == row.quote_asset,
            StrategyReplaySnapshot.dataset_schema_version == row.dataset_schema_version,
            StrategyReplaySnapshot.id > row.shadow_snapshot_id_watermark,
            StrategyReplaySnapshot.captured_at > row.shadow_enrolled_at,
            StrategyReplaySnapshot.captured_at > row.shadow_captured_at_watermark,
        )

    @staticmethod
    def _validate_snapshot(snapshot, validated):
        row = validated.row
        if (
            snapshot.dataset_schema_version != DATASET_SCHEMA_VERSION
            or not isinstance(snapshot.pipeline_run_id, str)
            or not snapshot.pipeline_run_id
            or not isinstance(snapshot.policy_signature, str)
            or not snapshot.policy_signature
            or not isinstance(snapshot.policy_data, dict)
            or snapshot.policy_data.get("dataset_schema_version")
            != snapshot.dataset_schema_version
            or policy_signature(snapshot.policy_data) != snapshot.policy_signature
        ):
            raise ShadowPolicyEvaluationError(
                f"snapshot provenance is invalid: {snapshot.id}"
            )
        _utc(snapshot.captured_at, "snapshot captured_at")
        try:
            _, stored_top_n = restore_weights(snapshot.policy_data)
        except Exception as error:
            raise ShadowPolicyEvaluationError(
                f"snapshot ranking policy is invalid: {snapshot.id}: {error}"
            ) from error
        matches = (
            snapshot.policy_signature == row.baseline_policy_signature
            and stored_top_n == row.effective_top_n
        )
        normalized_policy_data = {
            **snapshot.policy_data,
            "market_universe": {
                **snapshot.policy_data["market_universe"],
                "top_n": row.effective_top_n,
            },
        }
        baseline_policy_changed = (
            policy_signature(normalized_policy_data) != row.baseline_policy_signature
        )
        if matches:
            reason = None
        elif baseline_policy_changed and stored_top_n != row.effective_top_n:
            reason = "BASELINE_POLICY_AND_EFFECTIVE_TOP_N_CHANGED"
        elif baseline_policy_changed:
            reason = "BASELINE_POLICY_CHANGED"
        else:
            reason = "EFFECTIVE_TOP_N_CHANGED"
        return matches, reason, stored_top_n

    def _existing(self, enrollment_id):
        return tuple(
            self.session.scalars(
                select(ShadowPolicyEvaluation)
                .where(ShadowPolicyEvaluation.shadow_enrollment_id == enrollment_id)
                .order_by(
                    ShadowPolicyEvaluation.snapshot_captured_at,
                    ShadowPolicyEvaluation.strategy_replay_snapshot_id,
                )
                .execution_options(autoflush=False)
            )
        )

    def _plan(self, snapshot, context, replay, validated, evaluated_at):
        matches, reason, stored_top_n = context
        if not matches:
            values = dict(
                replay_status=NOT_REPLAYED,
                baseline_matches_stored=False,
                baseline_top_markets=[],
                shadow_top_markets=[],
                top_n_overlap_count=0,
                top_n_overlap_rate=Decimal("0"),
                entered_top_n=[],
                exited_top_n=[],
                rankable_candidate_count=0,
                evaluation_status=CONTEXT_MISMATCH,
                safe_reason=reason,
                scenario_signature=None,
            )
        else:
            if replay is None:
                raise ShadowPolicyEvaluationError(
                    "candidate-context replay result is missing"
                )
            self._validate_replay_identity(replay, snapshot)
            status = self._normalized_status(replay)
            if replay.compatible:
                self._validate_selection_metrics(replay, validated.row.effective_top_n)
            values = dict(
                replay_status=replay.status,
                baseline_matches_stored=replay.baseline_matches_stored,
                baseline_top_markets=list(replay.baseline_top_markets),
                shadow_top_markets=list(replay.scenario_top_markets),
                top_n_overlap_count=replay.top_n_overlap_count,
                top_n_overlap_rate=replay.top_n_overlap_rate,
                entered_top_n=list(replay.entered_top_n),
                exited_top_n=list(replay.exited_top_n),
                rankable_candidate_count=replay.rankable_candidate_count,
                evaluation_status=status,
                safe_reason=replay.safe_reason,
                scenario_signature=replay.scenario_signature,
            )
        enrollment = validated.row
        plan = ShadowPolicyEvaluation(
            evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
            shadow_enrollment_id=enrollment.id,
            candidate_id=enrollment.candidate_id,
            gate_decision_signature=enrollment.gate_decision_signature,
            shadow_enrolled_at=_utc(
                enrollment.shadow_enrolled_at, "shadow enrolled_at"
            ),
            shadow_snapshot_id_watermark=enrollment.shadow_snapshot_id_watermark,
            shadow_captured_at_watermark=_utc(
                enrollment.shadow_captured_at_watermark, "shadow captured watermark"
            ),
            strategy_replay_snapshot_id=snapshot.id,
            pipeline_run_id=snapshot.pipeline_run_id,
            snapshot_captured_at=_utc(snapshot.captured_at, "snapshot captured_at"),
            dataset_schema_version=snapshot.dataset_schema_version,
            snapshot_policy_signature=snapshot.policy_signature,
            snapshot_stored_top_n=stored_top_n,
            baseline_policy_signature=enrollment.baseline_policy_signature,
            effective_top_n=enrollment.effective_top_n,
            scenario_name=enrollment.scenario_name,
            scenario_definition_signature=enrollment.scenario_definition_signature,
            context_matches_enrollment=matches,
            evaluated_at=evaluated_at,
            **values,
        )
        plan.evaluation_signature = evaluation_signature(plan)
        return plan

    @staticmethod
    def _validate_replay_identity(replay, snapshot):
        if (
            replay.snapshot_id != snapshot.id
            or replay.pipeline_run_id != snapshot.pipeline_run_id
            or _utc(replay.captured_at, "replay captured_at")
            != _utc(snapshot.captured_at, "snapshot captured_at")
            or replay.dataset_schema_version != snapshot.dataset_schema_version
            or replay.baseline_policy_signature != snapshot.policy_signature
        ):
            raise ShadowPolicyEvaluationError(
                "Offline Replay snapshot identity is invalid"
            )

    @staticmethod
    def _normalized_status(replay):
        if replay.status == "BASELINE_MISMATCH" or (
            replay.status == "SUCCESS" and not replay.baseline_matches_stored
        ):
            return BASELINE_INTEGRITY_FAILED
        if replay.status == "SUCCESS":
            return SUCCESS
        return REPLAY_INCOMPATIBLE

    @staticmethod
    def _validate_selection_metrics(replay: SnapshotReplayResult, top_n: int):
        baseline = tuple(replay.baseline_top_markets)
        shadow = tuple(replay.scenario_top_markets)
        if (
            replay.effective_top_n != top_n
            or replay.stored_top_n != top_n
            or replay.requested_top_n != top_n
            or not isinstance(replay.scenario_signature, str)
            or not replay.scenario_signature
            or len(baseline) != top_n
            or len(shadow) != top_n
            or len(set(baseline)) != len(baseline)
            or len(set(shadow)) != len(shadow)
        ):
            raise ShadowPolicyEvaluationError(
                "Offline Replay TopN structure is invalid"
            )
        baseline_set = set(baseline)
        shadow_set = set(shadow)
        overlap = baseline_set & shadow_set
        expected_entered = tuple(sorted(shadow_set - baseline_set))
        expected_exited = tuple(sorted(baseline_set - shadow_set))
        expected_rate = Decimal(len(overlap)) / Decimal(top_n)
        if (
            replay.top_n_overlap_count != len(overlap)
            or replay.top_n_overlap_rate != expected_rate
            or tuple(replay.entered_top_n) != expected_entered
            or tuple(replay.exited_top_n) != expected_exited
            or set(replay.entered_top_n) & set(replay.exited_top_n)
        ):
            raise ShadowPolicyEvaluationError(
                "Offline Replay selection metrics are invalid"
            )

    @classmethod
    def _validate_evaluation(cls, row, validated):
        if evaluation_signature(row) != row.evaluation_signature:
            raise ShadowPolicyEvaluationError("evaluation signature does not verify")
        cls._validate_common_row(row, validated)
        if (
            row.evaluation_status
            not in {
                SUCCESS,
                CONTEXT_MISMATCH,
                BASELINE_INTEGRITY_FAILED,
                REPLAY_INCOMPATIBLE,
            }
            or isinstance(row.rankable_candidate_count, bool)
            or not isinstance(row.rankable_candidate_count, int)
            or row.rankable_candidate_count < 0
            or isinstance(row.top_n_overlap_count, bool)
            or not isinstance(row.top_n_overlap_count, int)
            or not isinstance(row.top_n_overlap_rate, Decimal)
            or not row.top_n_overlap_rate.is_finite()
        ):
            raise ShadowPolicyEvaluationError(
                "evaluation status or metrics are invalid"
            )
        if row.evaluation_status == SUCCESS:
            synthetic = SnapshotReplayResult(
                snapshot_id=row.strategy_replay_snapshot_id,
                pipeline_run_id=row.pipeline_run_id,
                captured_at=row.snapshot_captured_at,
                dataset_schema_version=row.dataset_schema_version,
                baseline_policy_signature=row.snapshot_policy_signature,
                scenario_signature=row.scenario_signature,
                status=row.replay_status,
                safe_reason=row.safe_reason,
                rankable_candidate_count=row.rankable_candidate_count,
                stored_top_n=row.snapshot_stored_top_n,
                requested_top_n=row.effective_top_n,
                effective_top_n=row.effective_top_n,
                held_augmented_count=0,
                baseline_matches_stored=row.baseline_matches_stored,
                baseline_top_markets=tuple(row.baseline_top_markets),
                scenario_top_markets=tuple(row.shadow_top_markets),
                top_n_overlap_count=row.top_n_overlap_count,
                top_n_overlap_rate=row.top_n_overlap_rate,
                entered_top_n=tuple(row.entered_top_n),
                exited_top_n=tuple(row.exited_top_n),
                candidate_results=(),
                mismatch_diagnostics=(),
            )
            if row.replay_status != SUCCESS or not row.baseline_matches_stored:
                raise ShadowPolicyEvaluationError("SUCCESS evaluation is inconsistent")
            cls._validate_selection_metrics(synthetic, row.effective_top_n)
        elif row.evaluation_status == CONTEXT_MISMATCH:
            if (
                row.context_matches_enrollment
                or row.replay_status != NOT_REPLAYED
                or row.baseline_matches_stored
                or row.scenario_signature is not None
                or row.top_n_overlap_count != 0
                or row.top_n_overlap_rate != 0
                or row.rankable_candidate_count != 0
                or any(
                    (
                        row.baseline_top_markets,
                        row.shadow_top_markets,
                        row.entered_top_n,
                        row.exited_top_n,
                    )
                )
            ):
                raise ShadowPolicyEvaluationError(
                    "context mismatch evaluation is inconsistent"
                )
        elif row.evaluation_status == BASELINE_INTEGRITY_FAILED:
            if (
                not row.context_matches_enrollment
                or row.baseline_matches_stored
                or row.replay_status not in {SUCCESS, "BASELINE_MISMATCH"}
            ):
                raise ShadowPolicyEvaluationError(
                    "baseline integrity evaluation is inconsistent"
                )
            synthetic = SnapshotReplayResult(
                snapshot_id=row.strategy_replay_snapshot_id,
                pipeline_run_id=row.pipeline_run_id,
                captured_at=row.snapshot_captured_at,
                dataset_schema_version=row.dataset_schema_version,
                baseline_policy_signature=row.snapshot_policy_signature,
                scenario_signature=row.scenario_signature,
                status=row.replay_status,
                safe_reason=row.safe_reason,
                rankable_candidate_count=row.rankable_candidate_count,
                stored_top_n=row.snapshot_stored_top_n,
                requested_top_n=row.effective_top_n,
                effective_top_n=row.effective_top_n,
                held_augmented_count=0,
                baseline_matches_stored=False,
                baseline_top_markets=tuple(row.baseline_top_markets),
                scenario_top_markets=tuple(row.shadow_top_markets),
                top_n_overlap_count=row.top_n_overlap_count,
                top_n_overlap_rate=row.top_n_overlap_rate,
                entered_top_n=tuple(row.entered_top_n),
                exited_top_n=tuple(row.exited_top_n),
                candidate_results=(),
                mismatch_diagnostics=(),
            )
            cls._validate_selection_metrics(synthetic, row.effective_top_n)
        elif (
            not row.context_matches_enrollment
            or row.replay_status in {SUCCESS, "BASELINE_MISMATCH", NOT_REPLAYED}
            or row.baseline_matches_stored
        ):
            raise ShadowPolicyEvaluationError(
                "replay incompatible evaluation is inconsistent"
            )

    @staticmethod
    def _validate_common_row(row, validated):
        enrollment = validated.row
        expected = (
            row.evaluation_schema_version == EVALUATION_SCHEMA_VERSION
            and row.shadow_enrollment_id == enrollment.id
            and row.candidate_id == enrollment.candidate_id
            and row.gate_decision_signature == enrollment.gate_decision_signature
            and _utc(row.shadow_enrolled_at, "stored shadow enrolled_at")
            == _utc(enrollment.shadow_enrolled_at, "shadow enrolled_at")
            and row.shadow_snapshot_id_watermark
            == enrollment.shadow_snapshot_id_watermark
            and _utc(
                row.shadow_captured_at_watermark, "stored shadow captured watermark"
            )
            == _utc(
                enrollment.shadow_captured_at_watermark, "shadow captured watermark"
            )
            and row.baseline_policy_signature == enrollment.baseline_policy_signature
            and row.effective_top_n == enrollment.effective_top_n
            and row.scenario_name == enrollment.scenario_name
            and row.scenario_definition_signature
            == enrollment.scenario_definition_signature
            and row.dataset_schema_version == enrollment.dataset_schema_version
            and row.strategy_replay_snapshot_id > row.shadow_snapshot_id_watermark
            and _utc(row.evaluated_at, "evaluation evaluated_at")
            >= _utc(enrollment.shadow_enrolled_at, "shadow enrolled_at")
            and _utc(row.snapshot_captured_at, "stored snapshot captured_at")
            > _utc(row.shadow_enrolled_at, "stored shadow enrolled_at")
            and _utc(row.snapshot_captured_at, "stored snapshot captured_at")
            > _utc(row.shadow_captured_at_watermark, "stored captured watermark")
        )
        if not expected:
            raise ShadowPolicyEvaluationError(
                "evaluation enrollment lineage is invalid"
            )

    @classmethod
    def _validate_existing(cls, row, snapshot, context, validated):
        if (
            row.strategy_replay_snapshot_id != snapshot.id
            or row.pipeline_run_id != snapshot.pipeline_run_id
            or _utc(row.snapshot_captured_at, "stored snapshot captured_at")
            != _utc(snapshot.captured_at, "snapshot captured_at")
            or row.dataset_schema_version != snapshot.dataset_schema_version
            or row.snapshot_policy_signature != snapshot.policy_signature
            or row.snapshot_stored_top_n != context[2]
            or row.context_matches_enrollment != context[0]
        ):
            raise ShadowPolicyEvaluationError(
                "stored evaluation snapshot lineage is invalid"
            )
        cls._validate_evaluation(row, validated)

    @staticmethod
    def _result(
        candidate_id,
        enrollment,
        status,
        safe_reason,
        *,
        preview_only,
        ceiling=None,
        snapshots=(),
        existing=(),
        plans=(),
        created_count=0,
        contexts=None,
    ):
        evaluations = tuple(
            sorted(
                (*existing, *plans),
                key=lambda row: (
                    _utc(row.snapshot_captured_at, "result snapshot captured_at"),
                    row.strategy_replay_snapshot_id,
                ),
            )
        )
        contexts = contexts or {}
        return ShadowPolicyEvaluationResult(
            candidate_id=candidate_id,
            enrollment=enrollment,
            evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
            evaluation_snapshot_id_ceiling=ceiling,
            timeline_snapshot_ids=tuple(row.id for row in snapshots),
            candidate_context_snapshot_ids=tuple(
                row.id for row in snapshots if contexts.get(row.id, (False,))[0]
            ),
            existing_evaluation_count=len(existing),
            planned_evaluation_count=len(plans),
            created_evaluation_count=created_count,
            success_count=sum(row.evaluation_status == SUCCESS for row in evaluations),
            context_mismatch_count=sum(
                row.evaluation_status == CONTEXT_MISMATCH for row in evaluations
            ),
            baseline_integrity_failed_count=sum(
                row.evaluation_status == BASELINE_INTEGRITY_FAILED
                for row in evaluations
            ),
            replay_incompatible_count=sum(
                row.evaluation_status == REPLAY_INCOMPATIBLE for row in evaluations
            ),
            status=status,
            safe_reason=safe_reason,
            evaluations=evaluations,
            preview_only=preview_only,
            database_write=created_count > 0,
        )


def validate_stored_shadow_policy_evaluation(row, snapshot, validated):
    """Validate immutable evaluation provenance for downstream Shadow evidence."""
    context = ShadowPolicyEvaluationService._validate_snapshot(snapshot, validated)
    ShadowPolicyEvaluationService._validate_existing(row, snapshot, context, validated)
    return context


__all__ = [
    "BASELINE_INTEGRITY_FAILED",
    "CONTEXT_MISMATCH",
    "DRY_RUN",
    "EVALUATION_SCHEMA_VERSION",
    "INVALID_SHADOW_EVALUATION",
    "NO_NEW_SHADOW_EVALUATIONS",
    "NO_POST_ENROLLMENT_SNAPSHOTS",
    "NO_SHADOW_ENROLLMENT",
    "REPLAY_INCOMPATIBLE",
    "SUCCESS",
    "ShadowPolicyEvaluationError",
    "ShadowPolicyEvaluationResult",
    "ShadowPolicyEvaluationService",
    "evaluation_signature",
    "validate_stored_shadow_policy_evaluation",
]
