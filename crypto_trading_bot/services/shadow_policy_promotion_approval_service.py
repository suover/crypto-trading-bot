from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import ShadowPolicyPromotionApproval
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentError,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    load_and_validate_shadow_policy_enrollment,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_REVIEW_DATA,
    NOT_ELIGIBLE,
    NO_SHADOW_ENROLLMENT,
    RESULT_TYPE as REVIEW_RESULT_TYPE,
    SHADOW_REVIEW_GATE_V1,
    ShadowReviewGateResult,
    ShadowReviewGateService,
    shadow_review_decision_payload,
    shadow_review_decision_signature,
    shadow_review_evidence_provenance,
    shadow_review_policy_definition,
    shadow_review_policy_signature,
)


REPORT_TYPE = "HUMAN_APPROVED_PROMOTION_V1"
APPROVAL_SCHEMA_VERSION = "human-approved-promotion-v1"
APPROVAL_SOURCE = "MANUAL_CLI"
CREATED = "CREATED"
ALREADY_APPROVED = "ALREADY_APPROVED"
DRY_RUN = "DRY_RUN"
REVIEW_NOT_ELIGIBLE = "REVIEW_NOT_ELIGIBLE"
REVIEW_DECISION_CHANGED = "REVIEW_DECISION_CHANGED"
INVALID_PROMOTION_APPROVAL = "INVALID_PROMOTION_APPROVAL"


class ShadowPolicyPromotionApprovalError(ValueError):
    pass


@dataclass(frozen=True)
class ShadowPolicyPromotionApprovalResult:
    candidate_id: int
    approval: ShadowPolicyPromotionApproval | None
    review: ShadowReviewGateResult | None
    approval_status: str
    safe_reason: str | None
    approval_schema_version: str
    expected_review_decision_signature: str | None
    current_review_decision_signature: str | None
    review_evaluated: bool
    review_eligible: bool
    review_signature_matched: bool
    human_approval_recorded: bool
    promotion_approval_created: bool
    promotion_approval_persisted: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    live_order_change: bool
    ranking_runtime_changed: bool
    shadow_runtime_changed: bool


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ShadowPolicyPromotionApprovalError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ShadowPolicyPromotionApprovalError("signature Decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonicalize(value):
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def review_checks_definition(review: ShadowReviewGateResult) -> list[dict]:
    return [_canonicalize(asdict(check)) for check in review.all_checks]


_APPROVAL_PAYLOAD_FIELDS = (
    "approval_schema_version",
    "candidate_id",
    "shadow_enrollment_id",
    "candidate_schema_version",
    "user_id",
    "exchange",
    "quote_asset",
    "scenario_name",
    "scenario_definition_signature",
    "component_weights",
    "dataset_schema_version",
    "baseline_policy_signature",
    "effective_top_n",
    "shadow_enrolled_at",
    "shadow_snapshot_id_watermark",
    "shadow_captured_at_watermark",
    "pre_shadow_gate_decision_signature",
    "review_result_type",
    "review_policy_schema_version",
    "review_policy_signature",
    "review_policy_definition",
    "review_status",
    "review_evaluated_at",
    "review_decision_signature",
    "review_decision_payload",
    "performance_evidence_as_of",
    "shadow_evaluation_snapshot_id_ceiling",
    "review_checks",
    "review_evidence_provenance",
    "approval_source",
    "human_approved_at",
)


def promotion_approval_payload(value) -> dict:
    return _canonicalize(
        {
            field_name: getattr(value, field_name)
            for field_name in _APPROVAL_PAYLOAD_FIELDS
        }
    )


def promotion_approval_signature(value) -> str:
    encoded = json.dumps(
        promotion_approval_payload(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{APPROVAL_SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"


def _review_signature_from_payload(payload: dict, schema_version: str) -> str:
    encoded = json.dumps(
        _canonicalize(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{schema_version}:{sha256(encoded).hexdigest()}"


def _valid_evidence_provenance(value) -> bool:
    expected_keys = {
        "timeline_snapshot_ids",
        "candidate_context_snapshot_ids",
        "successful_selection_snapshot_ids",
        "gross_successful_snapshot_ids_by_horizon",
        "turnover_transitions",
        "cost_adjustable_snapshot_ids_by_horizon",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    for key in (
        "timeline_snapshot_ids",
        "candidate_context_snapshot_ids",
        "successful_selection_snapshot_ids",
    ):
        items = value[key]
        if (
            not isinstance(items, list)
            or len(items) != len(set(items))
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 1
                for item in items
            )
        ):
            return False
    timeline_ids = set(value["timeline_snapshot_ids"])
    context_ids = set(value["candidate_context_snapshot_ids"])
    successful_ids = set(value["successful_selection_snapshot_ids"])
    if not context_ids.issubset(timeline_ids) or not successful_ids.issubset(
        context_ids
    ):
        return False
    for key in (
        "gross_successful_snapshot_ids_by_horizon",
        "cost_adjustable_snapshot_ids_by_horizon",
    ):
        items = value[key]
        if (
            not isinstance(items, list)
            or [item.get("horizon_minutes") for item in items] != [60, 240, 1440]
            or any(
                set(item) != {"horizon_minutes", "snapshot_ids"}
                or not isinstance(item["snapshot_ids"], list)
                or len(item["snapshot_ids"]) != len(set(item["snapshot_ids"]))
                or any(
                    isinstance(snapshot_id, bool)
                    or not isinstance(snapshot_id, int)
                    or snapshot_id < 1
                    for snapshot_id in item["snapshot_ids"]
                )
                for item in items
            )
        ):
            return False
        if any(
            not set(item["snapshot_ids"]).issubset(successful_ids) for item in items
        ):
            return False
    return isinstance(value["turnover_transitions"], list) and all(
        isinstance(item, dict)
        and set(item) == {"previous_snapshot_id", "current_snapshot_id"}
        and all(
            isinstance(item[key], int)
            and not isinstance(item[key], bool)
            and item[key] > 0
            for key in item
        )
        and item["previous_snapshot_id"] in timeline_ids
        and item["current_snapshot_id"] in timeline_ids
        and item["previous_snapshot_id"] != item["current_snapshot_id"]
        for item in value["turnover_transitions"]
    )


class ShadowPolicyPromotionApprovalService:
    def __init__(
        self,
        session: Session,
        *,
        review_service: ShadowReviewGateService | None = None,
        now_fn: Callable[[], datetime] | None = None,
        before_insert_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.review_service = review_service or ShadowReviewGateService(session)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.before_insert_fn = before_insert_fn

    def preview(self, *, candidate_id: int) -> ShadowPolicyPromotionApprovalResult:
        return self._execute(candidate_id=candidate_id, expected=None, apply=False)

    def approve(
        self, *, candidate_id: int, expected_review_decision_signature: str
    ) -> ShadowPolicyPromotionApprovalResult:
        self._validate_expected_signature(expected_review_decision_signature)
        return self._execute(
            candidate_id=candidate_id,
            expected=expected_review_decision_signature,
            apply=True,
        )

    def _execute(self, *, candidate_id: int, expected: str | None, apply: bool):
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        review = None
        try:
            existing = self._find_existing(candidate_id)
            if existing is not None:
                validated = load_and_validate_shadow_policy_enrollment(
                    self.session, candidate_id
                )
                if validated is None:
                    raise ShadowPolicyPromotionApprovalError(
                        "stored approval has no Shadow enrollment"
                    )
                self._validate_existing(existing, validated)
                if apply and expected != existing.review_decision_signature:
                    return self._result(
                        candidate_id,
                        existing,
                        None,
                        REVIEW_DECISION_CHANGED,
                        expected,
                        safe_reason="expected signature differs from stored approval",
                    )
                return self._result(
                    candidate_id,
                    existing,
                    None,
                    ALREADY_APPROVED,
                    expected,
                )

            review = self.review_service.evaluate(candidate_id=candidate_id)
            status = self._validate_review(review, candidate_id)
            if status == NO_SHADOW_ENROLLMENT:
                return self._result(
                    candidate_id, None, review, NO_SHADOW_ENROLLMENT, expected
                )
            if status == INVALID_REVIEW_DATA:
                raise ShadowPolicyPromotionApprovalError(
                    f"Shadow review is invalid: {review.safe_reason}"
                )
            if status in {INSUFFICIENT_DATA, NOT_ELIGIBLE}:
                return self._result(
                    candidate_id,
                    None,
                    review,
                    REVIEW_NOT_ELIGIBLE,
                    expected,
                    safe_reason=f"review status is {status}",
                )
            if not apply:
                return self._result(candidate_id, None, review, DRY_RUN, expected)
            if expected != review.review_decision_signature:
                return self._result(
                    candidate_id,
                    None,
                    review,
                    REVIEW_DECISION_CHANGED,
                    expected,
                    safe_reason="review decision changed after preview",
                )
            approved_at = _utc(self.now_fn(), "human approval clock")
            performance = review.performance
            if approved_at < _utc(
                review.evaluated_at, "review evaluated_at"
            ) or approved_at < _utc(
                performance.performance_evidence_as_of,
                "performance evidence as-of",
            ):
                raise ShadowPolicyPromotionApprovalError(
                    "human approval time precedes reviewed evidence"
                )
            approval = self._build_approval(review, approved_at)
            approval.approval_signature = promotion_approval_signature(approval)
            if self.before_insert_fn is not None:
                self.before_insert_fn()
            try:
                with self.session.begin_nested():
                    self.session.add(approval)
                    self.session.flush()
            except IntegrityError:
                concurrent = self._find_existing(candidate_id)
                if concurrent is None:
                    raise ShadowPolicyPromotionApprovalError(
                        "promotion approval uniqueness conflict"
                    ) from None
                validated = load_and_validate_shadow_policy_enrollment(
                    self.session, candidate_id
                )
                if validated is None:
                    raise ShadowPolicyPromotionApprovalError(
                        "concurrent approval has no enrollment"
                    )
                self._validate_existing(concurrent, validated)
                if concurrent.review_decision_signature != expected:
                    return self._result(
                        candidate_id,
                        concurrent,
                        review,
                        REVIEW_DECISION_CHANGED,
                        expected,
                        safe_reason="concurrent approval used a different decision",
                    )
                return self._result(
                    candidate_id,
                    concurrent,
                    review,
                    ALREADY_APPROVED,
                    expected,
                )
            return self._result(
                candidate_id, approval, review, CREATED, expected, created=True
            )
        except (
            ShadowPolicyEnrollmentError,
            ShadowPolicyPromotionApprovalError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            return self._result(
                candidate_id,
                None,
                review,
                INVALID_PROMOTION_APPROVAL,
                expected,
                safe_reason=str(error),
            )

    @staticmethod
    def _validate_expected_signature(value) -> None:
        prefix = f"{SHADOW_REVIEW_GATE_V1.schema_version}:"
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or not value.startswith(prefix)
            or len(value) != len(prefix) + 64
            or any(
                character not in "0123456789abcdef"
                for character in value[len(prefix) :]
            )
        ):
            raise ReplayInputError("expected review decision signature is invalid")

    def _validate_review(self, review, candidate_id):
        if (
            review.result_type != REVIEW_RESULT_TYPE
            or review.candidate_id != candidate_id
        ):
            raise ShadowPolicyPromotionApprovalError("review identity does not align")
        if review.status == NO_SHADOW_ENROLLMENT:
            return review.status
        if review.status not in {
            INVALID_REVIEW_DATA,
            INSUFFICIENT_DATA,
            NOT_ELIGIBLE,
            ELIGIBLE_FOR_PROMOTION_REVIEW,
        }:
            raise ShadowPolicyPromotionApprovalError("review status is unsupported")
        if (
            review.review_policy_schema_version != SHADOW_REVIEW_GATE_V1.schema_version
            or review.review_policy != SHADOW_REVIEW_GATE_V1
            or review.review_policy_signature
            != shadow_review_policy_signature(SHADOW_REVIEW_GATE_V1)
            or review.review_policy_definition
            != shadow_review_policy_definition(SHADOW_REVIEW_GATE_V1)
            or review.promotion_performed
            or review.database_write
            or review.external_calls
            or review.live_policy_change
            or review.shadow_runtime_changed
            or review.statistical_inference_performed
        ):
            raise ShadowPolicyPromotionApprovalError(
                "review safety provenance is invalid"
            )
        if review.status == INVALID_REVIEW_DATA:
            return review.status
        if not (
            review.enrollment is not None
            and review.enrollment.candidate_id == candidate_id
            and review.pre_shadow_gate_provenance_verified
            and review.shadow_performance_provenance_verified
            and review.sample_sufficiency_assessed
            and review.policy_decision_performed
            and review.performance is not None
            and review.performance.enrollment.id == review.enrollment.id
            and review.performance.candidate_id == candidate_id
            and review.review_decision_signature
            == shadow_review_decision_signature(review)
        ):
            raise ShadowPolicyPromotionApprovalError("review decision does not verify")
        if review.status == ELIGIBLE_FOR_PROMOTION_REVIEW and (
            not review.eligible_for_promotion_review
            or not review.all_checks
            or review.invalid_checks
            or review.insufficient_checks
            or review.failed_checks
            or tuple(review.passed_checks) != tuple(review.all_checks)
            or any(check.status != "PASS" for check in review.all_checks)
        ):
            raise ShadowPolicyPromotionApprovalError(
                "eligible review is internally inconsistent"
            )
        if (
            review.status != ELIGIBLE_FOR_PROMOTION_REVIEW
            and review.eligible_for_promotion_review
        ):
            raise ShadowPolicyPromotionApprovalError(
                "non-eligible review is inconsistent"
            )
        return review.status

    def _build_approval(self, review, approved_at):
        enrollment = review.enrollment
        performance = review.performance
        return ShadowPolicyPromotionApproval(
            approval_schema_version=APPROVAL_SCHEMA_VERSION,
            candidate_id=review.candidate_id,
            shadow_enrollment_id=enrollment.id,
            candidate_schema_version=enrollment.candidate_schema_version,
            user_id=enrollment.user_id,
            exchange=enrollment.exchange,
            quote_asset=enrollment.quote_asset,
            scenario_name=enrollment.scenario_name,
            scenario_definition_signature=enrollment.scenario_definition_signature,
            component_weights=_canonicalize(enrollment.component_weights),
            dataset_schema_version=enrollment.dataset_schema_version,
            baseline_policy_signature=enrollment.baseline_policy_signature,
            effective_top_n=enrollment.effective_top_n,
            shadow_enrolled_at=_utc(
                enrollment.shadow_enrolled_at, "shadow enrolled_at"
            ),
            shadow_snapshot_id_watermark=enrollment.shadow_snapshot_id_watermark,
            shadow_captured_at_watermark=_utc(
                enrollment.shadow_captured_at_watermark,
                "shadow captured watermark",
            ),
            pre_shadow_gate_decision_signature=enrollment.gate_decision_signature,
            review_result_type=review.result_type,
            review_policy_schema_version=review.review_policy_schema_version,
            review_policy_signature=review.review_policy_signature,
            review_policy_definition=review.review_policy_definition,
            review_status=review.status,
            review_evaluated_at=_utc(review.evaluated_at, "review evaluated_at"),
            review_decision_signature=review.review_decision_signature,
            review_decision_payload=shadow_review_decision_payload(review),
            performance_evidence_as_of=_utc(
                performance.performance_evidence_as_of,
                "performance evidence as-of",
            ),
            shadow_evaluation_snapshot_id_ceiling=(
                performance.shadow_evaluation_snapshot_id_ceiling
            ),
            review_checks=review_checks_definition(review),
            review_evidence_provenance=shadow_review_evidence_provenance(review),
            approval_source=APPROVAL_SOURCE,
            human_approved_at=approved_at,
            approval_signature="",
        )

    def _validate_existing(self, row, validated) -> None:
        enrollment = validated.row
        candidate = validated.candidate
        expected_identity = {
            "candidate_id": candidate.candidate_id,
            "shadow_enrollment_id": enrollment.id,
            "candidate_schema_version": candidate.candidate_schema_version,
            "user_id": candidate.user_id,
            "exchange": candidate.exchange,
            "quote_asset": candidate.quote_asset,
            "scenario_name": candidate.scenario_name,
            "scenario_definition_signature": candidate.scenario_definition_signature,
            "component_weights": _canonicalize(candidate.component_weights),
            "dataset_schema_version": candidate.dataset_schema_version,
            "baseline_policy_signature": candidate.baseline_policy_signature,
            "effective_top_n": candidate.effective_top_n,
            "shadow_enrolled_at": _utc(
                enrollment.shadow_enrolled_at, "shadow enrolled_at"
            ),
            "shadow_snapshot_id_watermark": enrollment.shadow_snapshot_id_watermark,
            "shadow_captured_at_watermark": _utc(
                enrollment.shadow_captured_at_watermark,
                "shadow captured watermark",
            ),
            "pre_shadow_gate_decision_signature": enrollment.gate_decision_signature,
        }
        check_keys = {
            "check_id",
            "category",
            "status",
            "horizon_minutes",
            "observed_value",
            "comparator",
            "threshold_value",
            "reason",
        }
        if (
            row.approval_schema_version != APPROVAL_SCHEMA_VERSION
            or any(
                _canonicalize(getattr(row, key)) != _canonicalize(value)
                for key, value in expected_identity.items()
            )
            or row.review_result_type != REVIEW_RESULT_TYPE
            or row.review_policy_schema_version != SHADOW_REVIEW_GATE_V1.schema_version
            or row.review_policy_signature
            != shadow_review_policy_signature(SHADOW_REVIEW_GATE_V1)
            or row.review_policy_definition
            != shadow_review_policy_definition(SHADOW_REVIEW_GATE_V1)
            or row.review_status != ELIGIBLE_FOR_PROMOTION_REVIEW
            or row.approval_source != APPROVAL_SOURCE
            or not isinstance(row.review_checks, list)
            or not row.review_checks
            or any(
                not isinstance(item, dict)
                or set(item) != check_keys
                or item.get("status") != "PASS"
                or not item.get("check_id")
                for item in row.review_checks
            )
            or not _valid_evidence_provenance(row.review_evidence_provenance)
            or not isinstance(row.shadow_evaluation_snapshot_id_ceiling, int)
            or isinstance(row.shadow_evaluation_snapshot_id_ceiling, bool)
            or row.shadow_evaluation_snapshot_id_ceiling < 1
        ):
            raise ShadowPolicyPromotionApprovalError(
                "stored promotion approval provenance is invalid"
            )
        review_payload = row.review_decision_payload
        payload_keys = {
            "result_type",
            "candidate",
            "stored_pre_shadow_gate_decision_signature",
            "review_policy_signature",
            "review_policy_definition",
            "performance_evidence_as_of",
            "shadow_evaluation_snapshot_id_ceiling",
            "successful_selection_snapshot_ids",
            "gross",
            "turnover",
            "cost",
            "checks",
            "status",
            "review_evaluated_at",
        }
        expected_review_candidate = {
            "candidate_id": row.candidate_id,
            "shadow_enrollment_id": row.shadow_enrollment_id,
            "user_id": row.user_id,
            "exchange": row.exchange,
            "quote_asset": row.quote_asset,
            "scenario_name": row.scenario_name,
            "scenario_definition_signature": row.scenario_definition_signature,
            "baseline_policy_signature": row.baseline_policy_signature,
            "effective_top_n": row.effective_top_n,
        }
        if (
            not isinstance(review_payload, dict)
            or set(review_payload) != payload_keys
            or review_payload.get("result_type") != row.review_result_type
            or review_payload.get("candidate") != expected_review_candidate
            or review_payload.get("stored_pre_shadow_gate_decision_signature")
            != row.pre_shadow_gate_decision_signature
            or review_payload.get("review_policy_signature")
            != row.review_policy_signature
            or review_payload.get("review_policy_definition")
            != row.review_policy_definition
            or review_payload.get("status") != row.review_status
            or review_payload.get("checks") != row.review_checks
            or review_payload.get("shadow_evaluation_snapshot_id_ceiling")
            != row.shadow_evaluation_snapshot_id_ceiling
            or review_payload.get("performance_evidence_as_of")
            != _utc(
                row.performance_evidence_as_of, "performance evidence as-of"
            ).isoformat()
            or review_payload.get("review_evaluated_at")
            != _utc(row.review_evaluated_at, "review evaluated_at").isoformat()
            or row.review_decision_signature
            != _review_signature_from_payload(
                review_payload, row.review_policy_schema_version
            )
            or _utc(row.human_approved_at, "human approved_at")
            < _utc(row.review_evaluated_at, "review evaluated_at")
            or _utc(row.human_approved_at, "human approved_at")
            < _utc(row.performance_evidence_as_of, "performance evidence as-of")
            or row.approval_signature != promotion_approval_signature(row)
        ):
            raise ShadowPolicyPromotionApprovalError(
                "stored promotion approval signature does not verify"
            )

    def _find_existing(self, candidate_id):
        return self.session.scalar(
            select(ShadowPolicyPromotionApproval)
            .where(ShadowPolicyPromotionApproval.candidate_id == candidate_id)
            .execution_options(autoflush=False)
        )

    @staticmethod
    def _result(
        candidate_id,
        approval,
        review,
        status,
        expected,
        *,
        safe_reason=None,
        created=False,
    ):
        current = (
            approval.review_decision_signature
            if approval is not None
            else getattr(review, "review_decision_signature", None)
        )
        recorded = approval is not None
        return ShadowPolicyPromotionApprovalResult(
            candidate_id=candidate_id,
            approval=approval,
            review=review,
            approval_status=status,
            safe_reason=safe_reason,
            approval_schema_version=APPROVAL_SCHEMA_VERSION,
            expected_review_decision_signature=expected,
            current_review_decision_signature=current,
            review_evaluated=review is not None,
            review_eligible=(
                getattr(review, "status", None) == ELIGIBLE_FOR_PROMOTION_REVIEW
                or recorded
            ),
            review_signature_matched=(expected is not None and expected == current),
            human_approval_recorded=recorded,
            promotion_approval_created=created,
            promotion_approval_persisted=recorded,
            database_write=created,
            external_calls=False,
            live_policy_change=False,
            live_order_change=False,
            ranking_runtime_changed=False,
            shadow_runtime_changed=False,
        )


__all__ = [
    "ALREADY_APPROVED",
    "APPROVAL_SCHEMA_VERSION",
    "APPROVAL_SOURCE",
    "CREATED",
    "DRY_RUN",
    "INVALID_PROMOTION_APPROVAL",
    "NO_SHADOW_ENROLLMENT",
    "REPORT_TYPE",
    "REVIEW_DECISION_CHANGED",
    "REVIEW_NOT_ELIGIBLE",
    "ShadowPolicyPromotionApprovalError",
    "ShadowPolicyPromotionApprovalResult",
    "ShadowPolicyPromotionApprovalService",
    "promotion_approval_payload",
    "promotion_approval_signature",
    "review_checks_definition",
]
