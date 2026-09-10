from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ShadowPolicyEnrollment,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    InvalidForwardCandidateProvenance,
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.policy_promotion_gate_service import (
    ELIGIBLE_FOR_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_PROMOTION_DATA,
    NOT_ELIGIBLE,
    POLICY_PROMOTION_GATE_V1,
    RESULT_TYPE as GATE_RESULT_TYPE,
    PolicyPromotionGateResult,
    PolicyPromotionGateService,
    gate_policy_signature,
)


ENROLLMENT_SCHEMA_VERSION = "shadow-policy-enrollment-v1"
DECISION_SIGNATURE_PREFIX = "shadow-enrollment-gate-decision-v1"
CREATED = "CREATED"
ALREADY_ENROLLED = "ALREADY_ENROLLED"
DRY_RUN = "DRY_RUN"
GATE_NOT_ELIGIBLE = "GATE_NOT_ELIGIBLE"
INVALID_SHADOW_ENROLLMENT = "INVALID_SHADOW_ENROLLMENT"


class ShadowPolicyEnrollmentError(ValueError):
    pass


@dataclass(frozen=True)
class ShadowPolicyEnrollmentResult:
    candidate_id: int
    candidate: ForwardCandidateMetadata | None
    enrollment: ShadowPolicyEnrollment | None
    gate: PolicyPromotionGateResult | None
    gate_decision_signature: str | None
    enrollment_status: str
    safe_reason: str | None
    enrollment_schema_version: str
    gate_evaluated: bool
    gate_eligible: bool
    shadow_enrollment_created: bool
    shadow_enrollment_persisted: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    promotion_performed: bool
    shadow_evaluation_started: bool


def _decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ShadowPolicyEnrollmentError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonicalize(value):
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def gate_policy_definition() -> dict:
    return _canonicalize(asdict(POLICY_PROMOTION_GATE_V1))


def gate_checks_definition(gate: PolicyPromotionGateResult) -> list[dict]:
    return [_canonicalize(asdict(check)) for check in gate.all_checks]


def gate_evidence_provenance(gate: PolicyPromotionGateResult) -> dict:
    historical = gate.historical
    gross = gate.forward_gross
    turnover = gate.forward_turnover
    cost = gate.forward_cost_adjusted
    return {
        "historical_candidate_snapshot_ids": list(
            getattr(historical, "historical_candidate_snapshot_ids", ())
        ),
        "forward_gross_eligible_snapshot_ids": list(
            getattr(gross, "eligible_forward_snapshot_ids", ())
        ),
        "forward_turnover_timeline_snapshot_ids": list(
            getattr(turnover, "forward_timeline_snapshot_ids", ())
        ),
        "forward_turnover_context_snapshot_ids": list(
            getattr(turnover, "candidate_context_snapshot_ids", ())
        ),
        "forward_cost_adjustable_snapshot_ids_by_horizon": [
            {
                "horizon_minutes": item.horizon_minutes,
                "snapshot_ids": list(item.cost_adjustable_forward_snapshot_ids),
            }
            for item in getattr(cost, "horizons", ())
        ],
    }


def _valid_stored_checks(value) -> bool:
    keys = {
        "check_id",
        "category",
        "status",
        "horizon_minutes",
        "observed_value",
        "comparator",
        "threshold_value",
        "reason",
    }
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict)
            and set(item) == keys
            and isinstance(item["check_id"], str)
            and bool(item["check_id"])
            and isinstance(item["category"], str)
            and bool(item["category"])
            and item["status"] == "PASS"
            and (
                item["horizon_minutes"] is None
                or (
                    isinstance(item["horizon_minutes"], int)
                    and not isinstance(item["horizon_minutes"], bool)
                    and item["horizon_minutes"] > 0
                )
            )
            for item in value
        )
    )


def _valid_evidence(value) -> bool:
    expected = {
        "historical_candidate_snapshot_ids",
        "forward_gross_eligible_snapshot_ids",
        "forward_turnover_timeline_snapshot_ids",
        "forward_turnover_context_snapshot_ids",
        "forward_cost_adjustable_snapshot_ids_by_horizon",
    }
    if not isinstance(value, dict) or set(value) != expected:
        return False
    id_lists = (
        value["historical_candidate_snapshot_ids"],
        value["forward_gross_eligible_snapshot_ids"],
        value["forward_turnover_timeline_snapshot_ids"],
        value["forward_turnover_context_snapshot_ids"],
    )
    if any(
        not isinstance(items, list)
        or len(items) != len(set(items))
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1
            for item in items
        )
        for items in id_lists
    ):
        return False
    horizons = value["forward_cost_adjustable_snapshot_ids_by_horizon"]
    return (
        isinstance(horizons, list)
        and [item.get("horizon_minutes") for item in horizons] == [60, 240, 1440]
        and all(
            isinstance(item, dict)
            and set(item) == {"horizon_minutes", "snapshot_ids"}
            and isinstance(item["snapshot_ids"], list)
            and len(item["snapshot_ids"]) == len(set(item["snapshot_ids"]))
            and all(
                isinstance(snapshot_id, int)
                and not isinstance(snapshot_id, bool)
                and snapshot_id > 0
                for snapshot_id in item["snapshot_ids"]
            )
            for item in horizons
        )
    )


def _candidate_definition(candidate: ForwardCandidateMetadata) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_schema_version": candidate.candidate_schema_version,
        "user_id": candidate.user_id,
        "exchange": candidate.exchange,
        "quote_asset": candidate.quote_asset,
        "scenario_name": candidate.scenario_name,
        "scenario_definition_signature": candidate.scenario_definition_signature,
        "component_weights": candidate.component_weights,
        "dataset_schema_version": candidate.dataset_schema_version,
        "baseline_policy_signature": candidate.baseline_policy_signature,
        "effective_top_n": candidate.effective_top_n,
        "candidate_registered_at": candidate.registered_at,
        "candidate_registration_snapshot_id_watermark": (
            candidate.registration_snapshot_id_watermark
        ),
        "candidate_registration_captured_at_watermark": (
            candidate.registration_captured_at_watermark
        ),
    }


def _decision_signature(payload: dict) -> str:
    encoded = json.dumps(
        _canonicalize(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{DECISION_SIGNATURE_PREFIX}:{sha256(encoded).hexdigest()}"


def gate_decision_signature(gate: PolicyPromotionGateResult) -> str:
    if gate.candidate is None:
        raise ShadowPolicyEnrollmentError("gate candidate metadata is missing")
    return _decision_signature(
        {
            "candidate": _candidate_definition(gate.candidate),
            "gate_result_type": GATE_RESULT_TYPE,
            "gate_policy_schema_version": gate.gate_policy_schema_version,
            "gate_policy_signature": gate.gate_policy_signature,
            "gate_policy_definition": _canonicalize(asdict(gate.gate_policy)),
            "gate_status": gate.status,
            "gate_evaluated_at": gate.evaluated_at,
            "gate_forward_snapshot_id_ceiling": (gate.forward_snapshot_id_ceiling),
            "gate_checks": gate_checks_definition(gate),
            "gate_evidence_provenance": gate_evidence_provenance(gate),
        }
    )


class ShadowPolicyEnrollmentService:
    def __init__(
        self,
        session: Session,
        *,
        gate_service=None,
        now_fn: Callable[[], datetime] | None = None,
        before_watermark_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.gate_service = gate_service or PolicyPromotionGateService(session)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.before_watermark_fn = before_watermark_fn

    def preview(self, *, candidate_id: int) -> ShadowPolicyEnrollmentResult:
        return self._execute(candidate_id=candidate_id, apply=False)

    def enroll(self, *, candidate_id: int) -> ShadowPolicyEnrollmentResult:
        return self._execute(candidate_id=candidate_id, apply=True)

    def _execute(self, *, candidate_id: int, apply: bool):
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        candidate = None
        gate = None
        try:
            validated = load_and_validate_forward_candidate(self.session, candidate_id)
            candidate = validated.metadata
            existing = self._find_existing(candidate_id)
            if existing is not None:
                self._validate_existing(existing, candidate)
                return self._result(
                    candidate,
                    existing,
                    None,
                    existing.gate_decision_signature,
                    ALREADY_ENROLLED,
                    gate_evaluated=False,
                )
            gate = self.gate_service.evaluate(candidate_id=candidate_id)
            signature = self._validate_gate(gate, candidate)
            if gate.status in (INSUFFICIENT_DATA, NOT_ELIGIBLE):
                return self._result(
                    candidate,
                    None,
                    gate,
                    signature,
                    GATE_NOT_ELIGIBLE,
                    safe_reason=f"gate status is {gate.status}",
                    gate_evaluated=True,
                )
            if gate.status != ELIGIBLE_FOR_REVIEW:
                raise ShadowPolicyEnrollmentError(
                    f"gate status is not enrollable: {gate.status}"
                )
            if not apply:
                return self._result(
                    candidate,
                    None,
                    gate,
                    signature,
                    DRY_RUN,
                    gate_evaluated=True,
                )
            enrolled_at = _utc(self.now_fn(), "shadow enrollment clock")
            if enrolled_at < _utc(gate.evaluated_at, "gate evaluated_at"):
                raise ShadowPolicyEnrollmentError(
                    "shadow enrollment time precedes gate evaluation"
                )
            if self.before_watermark_fn is not None:
                self.before_watermark_fn()
            id_watermark, captured_watermark = self._watermarks(candidate)
            if (
                id_watermark < gate.forward_snapshot_id_ceiling
                or id_watermark < candidate.registration_snapshot_id_watermark
                or captured_watermark
                < _utc(
                    candidate.registration_captured_at_watermark,
                    "candidate captured_at watermark",
                )
            ):
                raise ShadowPolicyEnrollmentError(
                    "shadow watermark precedes candidate or gate boundary"
                )
            candidate_values = _candidate_definition(candidate)
            candidate_values["component_weights"] = _canonicalize(
                candidate_values["component_weights"]
            )
            enrollment = ShadowPolicyEnrollment(
                enrollment_schema_version=ENROLLMENT_SCHEMA_VERSION,
                **candidate_values,
                gate_result_type=GATE_RESULT_TYPE,
                gate_policy_schema_version=gate.gate_policy_schema_version,
                gate_policy_signature=gate.gate_policy_signature,
                gate_policy_definition=gate_policy_definition(),
                gate_status=gate.status,
                gate_evaluated_at=_utc(gate.evaluated_at, "gate evaluated_at"),
                gate_forward_snapshot_id_ceiling=gate.forward_snapshot_id_ceiling,
                gate_checks=gate_checks_definition(gate),
                gate_evidence_provenance=gate_evidence_provenance(gate),
                gate_decision_signature=signature,
                shadow_enrolled_at=enrolled_at,
                shadow_snapshot_id_watermark=id_watermark,
                shadow_captured_at_watermark=captured_watermark,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(enrollment)
                    self.session.flush()
            except IntegrityError:
                concurrent = self._find_existing(candidate_id)
                if concurrent is None:
                    raise ShadowPolicyEnrollmentError(
                        "shadow enrollment uniqueness conflict"
                    ) from None
                self._validate_existing(concurrent, candidate)
                return self._result(
                    candidate,
                    concurrent,
                    None,
                    concurrent.gate_decision_signature,
                    ALREADY_ENROLLED,
                    gate_evaluated=True,
                )
            return self._result(
                candidate,
                enrollment,
                gate,
                signature,
                CREATED,
                gate_evaluated=True,
                created=True,
            )
        except (
            InvalidForwardCandidateProvenance,
            ShadowPolicyEnrollmentError,
            AttributeError,
            TypeError,
        ) as error:
            return self._result(
                candidate,
                None,
                gate,
                None,
                INVALID_SHADOW_ENROLLMENT,
                safe_reason=str(error),
                gate_evaluated=gate is not None,
                candidate_id=candidate_id,
            )

    def _validate_gate(self, gate, candidate):
        if gate.status == INVALID_PROMOTION_DATA:
            raise ShadowPolicyEnrollmentError(
                f"promotion gate data is invalid: {gate.safe_reason}"
            )
        if gate.candidate != candidate:
            raise ShadowPolicyEnrollmentError("gate candidate metadata does not align")
        if (
            gate.gate_policy_schema_version != POLICY_PROMOTION_GATE_V1.schema_version
            or gate.gate_policy != POLICY_PROMOTION_GATE_V1
            or gate.gate_policy_signature
            != gate_policy_signature(POLICY_PROMOTION_GATE_V1)
        ):
            raise ShadowPolicyEnrollmentError("gate v1 policy does not align")
        evaluated_at = _utc(gate.evaluated_at, "gate evaluated_at")
        if (
            isinstance(gate.forward_snapshot_id_ceiling, bool)
            or not isinstance(gate.forward_snapshot_id_ceiling, int)
            or gate.forward_snapshot_id_ceiling
            < candidate.registration_snapshot_id_watermark
        ):
            raise ShadowPolicyEnrollmentError("gate forward ceiling is invalid")
        if not (
            gate.sample_sufficiency_assessed
            and not gate.statistical_inference_performed
            and gate.policy_decision_performed
            and not gate.promotion_performed
            and not gate.shadow_policy_created
            and not gate.database_write
            and not gate.external_calls
            and not gate.live_policy_change
        ):
            raise ShadowPolicyEnrollmentError("gate safety flags are inconsistent")
        if gate.status == ELIGIBLE_FOR_REVIEW:
            if (
                gate.forward_snapshot_id_ceiling
                == candidate.registration_snapshot_id_watermark
                or not gate.all_checks
                or gate.invalid_checks
                or gate.insufficient_checks
                or gate.failed_checks
                or tuple(gate.passed_checks) != tuple(gate.all_checks)
                or any(check.status != "PASS" for check in gate.all_checks)
            ):
                raise ShadowPolicyEnrollmentError(
                    "eligible gate result is internally inconsistent"
                )
        elif gate.status not in (INSUFFICIENT_DATA, NOT_ELIGIBLE):
            raise ShadowPolicyEnrollmentError(f"unsupported gate status: {gate.status}")
        _ = evaluated_at
        return gate_decision_signature(gate)

    def _find_existing(self, candidate_id):
        return self.session.scalar(
            select(ShadowPolicyEnrollment)
            .where(ShadowPolicyEnrollment.candidate_id == candidate_id)
            .execution_options(autoflush=False)
        )

    def _watermarks(self, candidate):
        id_watermark, captured_watermark = self.session.execute(
            select(
                func.max(StrategyReplaySnapshot.id),
                func.max(StrategyReplaySnapshot.captured_at),
            )
            .where(
                StrategyReplaySnapshot.user_id == candidate.user_id,
                StrategyReplaySnapshot.exchange == candidate.exchange,
                StrategyReplaySnapshot.quote_asset == candidate.quote_asset,
                StrategyReplaySnapshot.dataset_schema_version
                == candidate.dataset_schema_version,
            )
            .execution_options(autoflush=False)
        ).one()
        if (
            isinstance(id_watermark, bool)
            or not isinstance(id_watermark, int)
            or not isinstance(captured_watermark, datetime)
        ):
            raise ShadowPolicyEnrollmentError("shadow snapshot watermark is invalid")
        return id_watermark, _utc(captured_watermark, "shadow captured_at watermark")

    def _validate_existing(self, row, candidate):
        expected_candidate = _canonicalize(_candidate_definition(candidate))
        stored_candidate = {key: getattr(row, key) for key in expected_candidate}
        policy_definition = gate_policy_definition()
        if (
            row.enrollment_schema_version != ENROLLMENT_SCHEMA_VERSION
            or _canonicalize(stored_candidate) != expected_candidate
            or row.gate_result_type != GATE_RESULT_TYPE
            or row.gate_policy_schema_version != POLICY_PROMOTION_GATE_V1.schema_version
            or row.gate_policy_signature
            != gate_policy_signature(POLICY_PROMOTION_GATE_V1)
            or row.gate_policy_definition != policy_definition
            or row.gate_status != ELIGIBLE_FOR_REVIEW
            or not _valid_stored_checks(row.gate_checks)
            or not _valid_evidence(row.gate_evidence_provenance)
        ):
            raise ShadowPolicyEnrollmentError(
                "stored shadow enrollment provenance is invalid"
            )
        payload = {
            "candidate": stored_candidate,
            "gate_result_type": row.gate_result_type,
            "gate_policy_schema_version": row.gate_policy_schema_version,
            "gate_policy_signature": row.gate_policy_signature,
            "gate_policy_definition": row.gate_policy_definition,
            "gate_status": row.gate_status,
            "gate_evaluated_at": row.gate_evaluated_at,
            "gate_forward_snapshot_id_ceiling": row.gate_forward_snapshot_id_ceiling,
            "gate_checks": row.gate_checks,
            "gate_evidence_provenance": row.gate_evidence_provenance,
        }
        if row.gate_decision_signature != _decision_signature(payload):
            raise ShadowPolicyEnrollmentError(
                "stored gate decision signature does not verify"
            )
        if (
            _utc(row.shadow_enrolled_at, "shadow enrolled_at")
            < _utc(row.gate_evaluated_at, "gate evaluated_at")
            or row.gate_forward_snapshot_id_ceiling
            < candidate.registration_snapshot_id_watermark
            or row.shadow_snapshot_id_watermark < row.gate_forward_snapshot_id_ceiling
            or _utc(row.shadow_captured_at_watermark, "shadow captured watermark")
            < _utc(
                candidate.registration_captured_at_watermark,
                "candidate captured watermark",
            )
        ):
            raise ShadowPolicyEnrollmentError("stored shadow anchor is invalid")

    @staticmethod
    def _result(
        candidate,
        enrollment,
        gate,
        signature,
        status,
        *,
        safe_reason=None,
        gate_evaluated,
        created=False,
        candidate_id=None,
    ):
        return ShadowPolicyEnrollmentResult(
            candidate_id=(candidate.candidate_id if candidate else candidate_id),
            candidate=candidate,
            enrollment=enrollment,
            gate=gate,
            gate_decision_signature=signature,
            enrollment_status=status,
            safe_reason=safe_reason,
            enrollment_schema_version=ENROLLMENT_SCHEMA_VERSION,
            gate_evaluated=gate_evaluated,
            gate_eligible=(status in (DRY_RUN, CREATED, ALREADY_ENROLLED)),
            shadow_enrollment_created=created,
            shadow_enrollment_persisted=enrollment is not None,
            database_write=created,
            external_calls=False,
            live_policy_change=False,
            promotion_performed=False,
            shadow_evaluation_started=False,
        )


__all__ = [
    "ALREADY_ENROLLED",
    "CREATED",
    "DRY_RUN",
    "ENROLLMENT_SCHEMA_VERSION",
    "GATE_NOT_ELIGIBLE",
    "INVALID_SHADOW_ENROLLMENT",
    "ShadowPolicyEnrollmentError",
    "ShadowPolicyEnrollmentResult",
    "ShadowPolicyEnrollmentService",
    "gate_decision_signature",
    "gate_policy_definition",
]
