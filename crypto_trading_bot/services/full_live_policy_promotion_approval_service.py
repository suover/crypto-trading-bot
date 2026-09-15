from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    FullLivePolicyPromotionApproval,
    LivePolicyCanaryActivation,
)
from crypto_trading_bot.services.live_canary_evidence_service import (
    EVIDENCE_SCHEMA_VERSION,
    live_canary_evidence_signature,
)
from crypto_trading_bot.services.live_canary_review_gate_service import (
    ELIGIBLE_FOR_FULL_LIVE_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_CANARY_DATA,
    LIVE_CANARY_REVIEW_GATE_V1,
    NOT_ELIGIBLE,
    NO_CANARY,
    RESULT_TYPE as REVIEW_RESULT_TYPE,
    LiveCanaryReviewGateResult,
    LiveCanaryReviewGateService,
    live_canary_review_decision_payload,
    live_canary_review_decision_signature,
    live_canary_review_decision_signature_from_payload,
    live_canary_review_policy_definition,
    live_canary_review_policy_signature,
)
from crypto_trading_bot.services.live_policy_canary_service import (
    load_and_validate_canary_safety_binding,
    validate_stored_canary_activation,
)
from crypto_trading_bot.services.live_policy_canary_termination_service import (
    load_and_validate_canary_termination,
)


REPORT_TYPE = "HUMAN_APPROVED_FULL_LIVE_PROMOTION_V1"
APPROVAL_SCHEMA_VERSION = "human-approved-full-live-promotion-v1"
APPROVAL_SOURCE = "MANUAL_CLI"
DRY_RUN = "DRY_RUN"
CREATED = "CREATED"
ALREADY_APPROVED = "ALREADY_APPROVED"
REVIEW_NOT_ELIGIBLE = "REVIEW_NOT_ELIGIBLE"
CONFIRMATION_MISMATCH = "CONFIRMATION_MISMATCH"
INTERACTIVE_CONFIRMATION_REQUIRED = "INTERACTIVE_CONFIRMATION_REQUIRED"
APPROVAL_CONFLICT = "APPROVAL_CONFLICT"
INVALID_FULL_LIVE_PROMOTION_APPROVAL = "INVALID_FULL_LIVE_PROMOTION_APPROVAL"


class FullLivePolicyPromotionApprovalError(ValueError):
    pass


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FullLivePolicyPromotionApprovalError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise FullLivePolicyPromotionApprovalError("signature Decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonicalize(value):
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _valid_review_signature(value: object) -> bool:
    prefix = f"{LIVE_CANARY_REVIEW_GATE_V1.schema_version}:"
    return (
        isinstance(value, str)
        and value == value.strip()
        and value.startswith(prefix)
        and len(value) == len(prefix) + 64
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


@dataclass(frozen=True)
class FullLivePromotionReviewArtifact:
    canary_activation_id: int
    canary_activation_signature: str
    safety_binding_id: int
    safety_binding_signature: str
    promotion_approval_id: int
    promotion_approval_signature: str
    candidate_id: int
    user_id: int
    exchange: str
    quote_asset: str
    scenario_name: str
    scenario_definition_signature: str
    baseline_policy_signature: str
    canary_policy_signature: str
    effective_top_n: int
    termination_event_id: int | None
    termination_signature: str | None
    evidence_schema_version: str
    evidence_signature: str
    evidence_as_of: datetime
    review_result_type: str
    review_policy_schema_version: str
    review_policy_signature: str
    review_policy_definition: Mapping[str, Any]
    review_status: str
    eligible_for_full_live_review: bool
    review_evaluated_at: datetime
    review_decision_signature: str
    review_decision_payload: Mapping[str, Any]
    review_checks: tuple[Mapping[str, Any], ...]


def _require_mapping(value, field_name):
    if not isinstance(value, Mapping):
        raise FullLivePolicyPromotionApprovalError(f"{field_name} must be a mapping")
    return value


def freeze_full_live_promotion_review_artifact(
    review: LiveCanaryReviewGateResult,
    *,
    canary_activation_id: int | None = None,
) -> FullLivePromotionReviewArtifact:
    requested_id = (
        review.canary_activation_id
        if canary_activation_id is None
        else canary_activation_id
    )
    if review.status in {
        NO_CANARY,
        INVALID_CANARY_DATA,
        INSUFFICIENT_DATA,
        NOT_ELIGIBLE,
    }:
        raise FullLivePolicyPromotionApprovalError(
            f"review status is not eligible: {review.status}"
        )
    if (
        review.result_type != REVIEW_RESULT_TYPE
        or review.canary_activation_id != requested_id
        or review.candidate_id is None
        or review.evidence_schema_version != EVIDENCE_SCHEMA_VERSION
        or review.review_policy_schema_version
        != LIVE_CANARY_REVIEW_GATE_V1.schema_version
        or review.review_policy != LIVE_CANARY_REVIEW_GATE_V1
        or review.review_policy_signature
        != live_canary_review_policy_signature(LIVE_CANARY_REVIEW_GATE_V1)
        or review.review_policy_definition
        != live_canary_review_policy_definition(LIVE_CANARY_REVIEW_GATE_V1)
        or review.status != ELIGIBLE_FOR_FULL_LIVE_REVIEW
        or not review.eligible_for_full_live_review
        or not review.evidence_provenance_verified
        or not review.sample_sufficiency_assessed
        or not review.policy_decision_performed
        or review.statistical_inference_performed
        or review.invalid_checks
        or review.failed_checks
        or review.insufficient_checks
        or not review.all_checks
        or tuple(review.passed_checks) != tuple(review.all_checks)
        or any(check.status != "PASS" for check in review.all_checks)
        or review.database_write
        or review.external_calls
        or review.promotion_performed
        or review.full_live_promotion_performed
        or review.live_policy_change
        or review.live_order_change
        or review.ranking_runtime_changed
        or review.canary_state_changed
    ):
        raise FullLivePolicyPromotionApprovalError(
            "eligible LIVE Canary Review is internally inconsistent"
        )
    if not _valid_review_signature(review.review_decision_signature):
        raise FullLivePolicyPromotionApprovalError(
            "Review decision signature is invalid"
        )
    if review.review_decision_signature != live_canary_review_decision_signature(
        review
    ):
        raise FullLivePolicyPromotionApprovalError(
            "Review decision signature does not verify"
        )
    evidence = review.evidence
    evidence_data = evidence.as_dict()
    evidence_signature = evidence_data.get("evidence_signature")
    unsigned_evidence = dict(evidence_data)
    unsigned_evidence.pop("evidence_signature", None)
    if (
        evidence_signature != review.evidence_signature
        or evidence_signature != live_canary_evidence_signature(unsigned_evidence)
        or evidence_data.get("canary_activation_id") != requested_id
        or evidence_data.get("candidate_id") != review.candidate_id
        or evidence_data.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION
    ):
        raise FullLivePolicyPromotionApprovalError(
            "Evidence signature or identity is invalid"
        )
    activation = _require_mapping(
        evidence_data.get("activation_provenance"), "activation provenance"
    )
    binding = _require_mapping(
        evidence_data.get("safety_binding_provenance"), "safety binding provenance"
    )
    promotion = _require_mapping(
        evidence_data.get("promotion_provenance"), "promotion provenance"
    )
    termination = evidence_data.get("termination_provenance")
    if termination is not None:
        termination = _require_mapping(termination, "termination provenance")
    decision_payload = live_canary_review_decision_payload(review)
    if (
        decision_payload.get("evidence_signature") != evidence_signature
        or live_canary_review_decision_signature_from_payload(decision_payload)
        != review.review_decision_signature
    ):
        raise FullLivePolicyPromotionApprovalError("Review decision payload is invalid")
    evidence_as_of = _utc(review.evidence_as_of, "Evidence as-of")
    evaluated_at = _utc(review.evaluated_at, "Review evaluated_at")
    if evaluated_at < evidence_as_of:
        raise FullLivePolicyPromotionApprovalError("Review predates its Evidence")
    checks = [_canonicalize(asdict(check)) for check in review.all_checks]
    return FullLivePromotionReviewArtifact(
        canary_activation_id=requested_id,
        canary_activation_signature=str(activation["activation_signature"]),
        safety_binding_id=int(binding["safety_binding_id"]),
        safety_binding_signature=str(binding["safety_binding_signature"]),
        promotion_approval_id=int(promotion["promotion_approval_id"]),
        promotion_approval_signature=str(promotion["promotion_approval_signature"]),
        candidate_id=review.candidate_id,
        user_id=int(activation["user_id"]),
        exchange=str(activation["exchange"]),
        quote_asset=str(activation["quote_asset"]),
        scenario_name=str(activation["scenario_name"]),
        scenario_definition_signature=str(activation["scenario_definition_signature"]),
        baseline_policy_signature=str(activation["baseline_policy_signature"]),
        canary_policy_signature=str(activation["canary_policy_signature"]),
        effective_top_n=int(activation["effective_top_n"]),
        termination_event_id=(
            None if termination is None else int(termination["termination_event_id"])
        ),
        termination_signature=(
            None if termination is None else str(termination["termination_signature"])
        ),
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        evidence_signature=evidence_signature,
        evidence_as_of=evidence_as_of,
        review_result_type=review.result_type,
        review_policy_schema_version=review.review_policy_schema_version,
        review_policy_signature=review.review_policy_signature,
        review_policy_definition=_freeze(
            _canonicalize(review.review_policy_definition)
        ),
        review_status=review.status,
        eligible_for_full_live_review=True,
        review_evaluated_at=evaluated_at,
        review_decision_signature=review.review_decision_signature,
        review_decision_payload=_freeze(_canonicalize(decision_payload)),
        review_checks=tuple(_freeze(item) for item in checks),
    )


_APPROVAL_PAYLOAD_FIELDS = (
    "approval_schema_version",
    "canary_activation_id",
    "canary_activation_signature",
    "safety_binding_id",
    "safety_binding_signature",
    "promotion_approval_id",
    "promotion_approval_signature",
    "candidate_id",
    "user_id",
    "exchange",
    "quote_asset",
    "scenario_name",
    "scenario_definition_signature",
    "baseline_policy_signature",
    "canary_policy_signature",
    "effective_top_n",
    "termination_event_id",
    "termination_signature",
    "evidence_schema_version",
    "evidence_signature",
    "evidence_as_of",
    "review_result_type",
    "review_policy_schema_version",
    "review_policy_signature",
    "review_policy_definition",
    "review_status",
    "review_evaluated_at",
    "review_decision_signature",
    "review_decision_payload",
    "review_checks",
    "approval_source",
    "human_approved_at",
)


def full_live_promotion_approval_payload(value) -> dict:
    return _canonicalize(
        {
            field_name: getattr(value, field_name)
            for field_name in _APPROVAL_PAYLOAD_FIELDS
        }
    )


def full_live_promotion_approval_signature(value) -> str:
    encoded = json.dumps(
        full_live_promotion_approval_payload(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{APPROVAL_SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class ValidatedFullLivePolicyPromotionApproval:
    row: FullLivePolicyPromotionApproval
    activation: object
    safety_binding: object
    promotion_approval: object
    candidate: object
    termination: object | None


@dataclass(frozen=True)
class FullLivePolicyPromotionApprovalResult:
    canary_activation_id: int
    candidate_id: int | None
    approval: FullLivePolicyPromotionApproval | None
    artifact: FullLivePromotionReviewArtifact | None
    review: object | None
    approval_status: str
    safe_reason: str | None
    approval_schema_version: str
    review_status: str | None
    evidence_signature: str | None
    review_policy_signature: str | None
    review_decision_signature: str | None
    confirmation_required: bool
    confirmation_matched: bool
    human_approval_recorded: bool
    full_live_promotion_approval_created: bool
    full_live_promotion_approval_persisted: bool
    full_live_policy_activated: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    live_order_change: bool
    ranking_runtime_changed: bool
    canary_state_changed: bool


class FullLivePolicyPromotionApprovalService:
    """Persist one already-frozen Review artifact; never evaluates Evidence or Review."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        now_fn: Callable[[], datetime] | None = None,
        before_insert_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.before_insert_fn = before_insert_fn

    def find_existing(self, canary_activation_id: int):
        return self.session.scalar(
            select(FullLivePolicyPromotionApproval)
            .where(
                FullLivePolicyPromotionApproval.canary_activation_id
                == canary_activation_id
            )
            .execution_options(autoflush=False)
        )

    def approve_artifact(
        self,
        *,
        artifact: FullLivePromotionReviewArtifact,
        confirmed_review_decision_signature: str,
    ) -> FullLivePolicyPromotionApprovalResult:
        try:
            self._validate_artifact(artifact)
            if not _valid_review_signature(confirmed_review_decision_signature):
                return self._result(
                    artifact.canary_activation_id,
                    artifact,
                    CONFIRMATION_MISMATCH,
                    safe_reason="confirmation signature format is invalid",
                )
            if (
                confirmed_review_decision_signature
                != artifact.review_decision_signature
            ):
                return self._result(
                    artifact.canary_activation_id,
                    artifact,
                    CONFIRMATION_MISMATCH,
                    safe_reason="confirmation does not match the frozen Review",
                )
            existing = self.find_existing(artifact.canary_activation_id)
            if existing is not None:
                validated = self.validate_stored_approval(existing)
                status = (
                    ALREADY_APPROVED
                    if validated.row.review_decision_signature
                    == artifact.review_decision_signature
                    else APPROVAL_CONFLICT
                )
                return self._result(
                    artifact.canary_activation_id,
                    artifact,
                    status,
                    approval=validated.row,
                    safe_reason=(
                        None
                        if status == ALREADY_APPROVED
                        else "activation was approved from a different Review artifact"
                    ),
                )
            lineage = self._validate_current_lineage(artifact)
            approved_at = _utc(self.now_fn(), "human approval clock")
            if approved_at < artifact.review_evaluated_at:
                raise FullLivePolicyPromotionApprovalError(
                    "human approval time precedes the reviewed decision"
                )
            approval = self._build_approval(artifact, approved_at)
            approval.approval_signature = full_live_promotion_approval_signature(
                approval
            )
            if self.before_insert_fn is not None:
                self.before_insert_fn()
            try:
                with self.session.begin_nested():
                    self.session.add(approval)
                    self.session.flush()
            except IntegrityError:
                concurrent = self.find_existing(artifact.canary_activation_id)
                if concurrent is None:
                    raise FullLivePolicyPromotionApprovalError(
                        "Full LIVE promotion approval uniqueness conflict"
                    ) from None
                validated = self.validate_stored_approval(concurrent)
                status = (
                    ALREADY_APPROVED
                    if validated.row.review_decision_signature
                    == artifact.review_decision_signature
                    else APPROVAL_CONFLICT
                )
                return self._result(
                    artifact.canary_activation_id,
                    artifact,
                    status,
                    approval=validated.row,
                    safe_reason=(
                        None
                        if status == ALREADY_APPROVED
                        else "concurrent approval used a different Review artifact"
                    ),
                )
            del lineage
            return self._result(
                artifact.canary_activation_id,
                artifact,
                CREATED,
                approval=approval,
                created=True,
            )
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            return self._result(
                getattr(artifact, "canary_activation_id", 0),
                artifact
                if isinstance(artifact, FullLivePromotionReviewArtifact)
                else None,
                INVALID_FULL_LIVE_PROMOTION_APPROVAL,
                safe_reason=str(error),
            )

    def _validate_current_lineage(self, artifact):
        activation = self.session.scalar(
            select(LivePolicyCanaryActivation)
            .where(LivePolicyCanaryActivation.id == artifact.canary_activation_id)
            .execution_options(autoflush=False)
        )
        if activation is None:
            raise FullLivePolicyPromotionApprovalError(
                "Canary activation does not exist"
            )
        validated, _, _ = validate_stored_canary_activation(
            self.session, activation, self.settings
        )
        binding = load_and_validate_canary_safety_binding(self.session, activation)
        termination = load_and_validate_canary_termination(
            self.session, activation, binding
        )
        promotion = validated.row
        candidate = validated.candidate
        expected = {
            "canary_activation_signature": activation.activation_signature,
            "safety_binding_id": binding.id,
            "safety_binding_signature": binding.binding_signature,
            "promotion_approval_id": promotion.id,
            "promotion_approval_signature": promotion.approval_signature,
            "candidate_id": candidate.candidate_id,
            "user_id": activation.user_id,
            "exchange": activation.exchange,
            "quote_asset": activation.quote_asset,
            "scenario_name": activation.scenario_name,
            "scenario_definition_signature": activation.scenario_definition_signature,
            "baseline_policy_signature": activation.baseline_policy_signature,
            "canary_policy_signature": activation.canary_policy_signature,
            "effective_top_n": activation.effective_top_n,
            "termination_event_id": getattr(termination, "id", None),
            "termination_signature": getattr(
                termination, "termination_signature", None
            ),
        }
        if any(getattr(artifact, name) != value for name, value in expected.items()):
            raise FullLivePolicyPromotionApprovalError(
                "frozen Review provenance no longer matches immutable DB lineage"
            )
        return activation, binding, promotion, candidate, termination

    @staticmethod
    def _validate_artifact(artifact):
        if not isinstance(artifact, FullLivePromotionReviewArtifact):
            raise FullLivePolicyPromotionApprovalError("Review artifact is invalid")
        payload = _thaw(artifact.review_decision_payload)
        checks = _thaw(artifact.review_checks)
        expected_payload = {
            "result_type": artifact.review_result_type,
            "canary_activation_id": artifact.canary_activation_id,
            "candidate_id": artifact.candidate_id,
            "evidence_schema_version": artifact.evidence_schema_version,
            "evidence_signature": artifact.evidence_signature,
            "evidence_as_of": artifact.evidence_as_of,
            "review_policy_schema_version": artifact.review_policy_schema_version,
            "review_policy_signature": artifact.review_policy_signature,
            "review_policy_definition": _thaw(artifact.review_policy_definition),
            "checks": checks,
            "status": artifact.review_status,
            "eligible_for_full_live_review": (artifact.eligible_for_full_live_review),
            "review_evaluated_at": artifact.review_evaluated_at,
        }
        if (
            artifact.review_result_type != REVIEW_RESULT_TYPE
            or artifact.review_status != ELIGIBLE_FOR_FULL_LIVE_REVIEW
            or not artifact.eligible_for_full_live_review
            or artifact.evidence_schema_version != EVIDENCE_SCHEMA_VERSION
            or artifact.review_policy_schema_version
            != LIVE_CANARY_REVIEW_GATE_V1.schema_version
            or _thaw(artifact.review_policy_definition)
            != live_canary_review_policy_definition(LIVE_CANARY_REVIEW_GATE_V1)
            or artifact.review_policy_signature
            != live_canary_review_policy_signature(LIVE_CANARY_REVIEW_GATE_V1)
            or any(
                _canonicalize(payload.get(name)) != _canonicalize(value)
                for name, value in expected_payload.items()
            )
            or live_canary_review_decision_signature_from_payload(payload)
            != artifact.review_decision_signature
            or not checks
            or any(item.get("status") != "PASS" for item in checks)
            or artifact.review_evaluated_at < artifact.evidence_as_of
        ):
            raise FullLivePolicyPromotionApprovalError(
                "frozen Review artifact is invalid"
            )

    @staticmethod
    def _build_approval(artifact, approved_at):
        return FullLivePolicyPromotionApproval(
            approval_schema_version=APPROVAL_SCHEMA_VERSION,
            canary_activation_id=artifact.canary_activation_id,
            canary_activation_signature=artifact.canary_activation_signature,
            safety_binding_id=artifact.safety_binding_id,
            safety_binding_signature=artifact.safety_binding_signature,
            promotion_approval_id=artifact.promotion_approval_id,
            promotion_approval_signature=artifact.promotion_approval_signature,
            candidate_id=artifact.candidate_id,
            user_id=artifact.user_id,
            exchange=artifact.exchange,
            quote_asset=artifact.quote_asset,
            scenario_name=artifact.scenario_name,
            scenario_definition_signature=artifact.scenario_definition_signature,
            baseline_policy_signature=artifact.baseline_policy_signature,
            canary_policy_signature=artifact.canary_policy_signature,
            effective_top_n=artifact.effective_top_n,
            termination_event_id=artifact.termination_event_id,
            termination_signature=artifact.termination_signature,
            evidence_schema_version=artifact.evidence_schema_version,
            evidence_signature=artifact.evidence_signature,
            evidence_as_of=artifact.evidence_as_of,
            review_result_type=artifact.review_result_type,
            review_policy_schema_version=artifact.review_policy_schema_version,
            review_policy_signature=artifact.review_policy_signature,
            review_policy_definition=_thaw(artifact.review_policy_definition),
            review_status=artifact.review_status,
            review_evaluated_at=artifact.review_evaluated_at,
            review_decision_signature=artifact.review_decision_signature,
            review_decision_payload=_thaw(artifact.review_decision_payload),
            review_checks=_thaw(artifact.review_checks),
            approval_source=APPROVAL_SOURCE,
            human_approved_at=approved_at,
            approval_signature="",
        )

    def validate_stored_approval(self, row):
        artifact = FullLivePromotionReviewArtifact(
            canary_activation_id=row.canary_activation_id,
            canary_activation_signature=row.canary_activation_signature,
            safety_binding_id=row.safety_binding_id,
            safety_binding_signature=row.safety_binding_signature,
            promotion_approval_id=row.promotion_approval_id,
            promotion_approval_signature=row.promotion_approval_signature,
            candidate_id=row.candidate_id,
            user_id=row.user_id,
            exchange=row.exchange,
            quote_asset=row.quote_asset,
            scenario_name=row.scenario_name,
            scenario_definition_signature=row.scenario_definition_signature,
            baseline_policy_signature=row.baseline_policy_signature,
            canary_policy_signature=row.canary_policy_signature,
            effective_top_n=row.effective_top_n,
            termination_event_id=row.termination_event_id,
            termination_signature=row.termination_signature,
            evidence_schema_version=row.evidence_schema_version,
            evidence_signature=row.evidence_signature,
            evidence_as_of=_utc(row.evidence_as_of, "stored Evidence as-of"),
            review_result_type=row.review_result_type,
            review_policy_schema_version=row.review_policy_schema_version,
            review_policy_signature=row.review_policy_signature,
            review_policy_definition=_freeze(row.review_policy_definition),
            review_status=row.review_status,
            eligible_for_full_live_review=True,
            review_evaluated_at=_utc(
                row.review_evaluated_at, "stored Review evaluated_at"
            ),
            review_decision_signature=row.review_decision_signature,
            review_decision_payload=_freeze(row.review_decision_payload),
            review_checks=tuple(_freeze(item) for item in row.review_checks),
        )
        self._validate_artifact(artifact)
        lineage = self._validate_current_lineage(artifact)
        if (
            row.approval_schema_version != APPROVAL_SCHEMA_VERSION
            or row.approval_source != APPROVAL_SOURCE
            or _utc(row.human_approved_at, "stored human approved_at")
            < artifact.review_evaluated_at
            or row.approval_signature != full_live_promotion_approval_signature(row)
        ):
            raise FullLivePolicyPromotionApprovalError(
                "stored Full LIVE promotion approval does not verify"
            )
        return ValidatedFullLivePolicyPromotionApproval(
            row=row,
            activation=lineage[0],
            safety_binding=lineage[1],
            promotion_approval=lineage[2],
            candidate=lineage[3],
            termination=lineage[4],
        )

    @staticmethod
    def _result(
        activation_id,
        artifact,
        status,
        *,
        approval=None,
        review=None,
        safe_reason=None,
        created=False,
    ):
        recorded = approval is not None
        return FullLivePolicyPromotionApprovalResult(
            canary_activation_id=activation_id,
            candidate_id=(
                getattr(artifact, "candidate_id", None)
                or getattr(approval, "candidate_id", None)
                or getattr(review, "candidate_id", None)
            ),
            approval=approval,
            artifact=artifact,
            review=review,
            approval_status=status,
            safe_reason=safe_reason,
            approval_schema_version=APPROVAL_SCHEMA_VERSION,
            review_status=(
                getattr(artifact, "review_status", None)
                or getattr(approval, "review_status", None)
                or getattr(review, "status", None)
            ),
            evidence_signature=(
                getattr(artifact, "evidence_signature", None)
                or getattr(approval, "evidence_signature", None)
            ),
            review_policy_signature=(
                getattr(artifact, "review_policy_signature", None)
                or getattr(approval, "review_policy_signature", None)
            ),
            review_decision_signature=(
                getattr(artifact, "review_decision_signature", None)
                or getattr(approval, "review_decision_signature", None)
            ),
            confirmation_required=status
            in {DRY_RUN, INTERACTIVE_CONFIRMATION_REQUIRED},
            confirmation_matched=status in {CREATED, ALREADY_APPROVED},
            human_approval_recorded=recorded,
            full_live_promotion_approval_created=created,
            full_live_promotion_approval_persisted=recorded,
            full_live_policy_activated=False,
            database_write=created,
            external_calls=False,
            live_policy_change=False,
            live_order_change=False,
            ranking_runtime_changed=False,
            canary_state_changed=False,
        )


def load_and_validate_full_live_policy_promotion_approval(
    session: Session,
    approval_id: int,
    *,
    settings: Settings | None = None,
) -> ValidatedFullLivePolicyPromotionApproval | None:
    if (
        isinstance(approval_id, bool)
        or not isinstance(approval_id, int)
        or approval_id < 1
    ):
        raise FullLivePolicyPromotionApprovalError("approval ID must be positive")
    row = session.scalar(
        select(FullLivePolicyPromotionApproval)
        .where(FullLivePolicyPromotionApproval.id == approval_id)
        .execution_options(autoflush=False)
    )
    if row is None:
        return None
    return FullLivePolicyPromotionApprovalService(
        session, settings=settings
    ).validate_stored_approval(row)


class FullLivePolicyPromotionApprovalWorkflow:
    """Orchestrate separate lookup, read-only Review, and write transactions."""

    def __init__(
        self,
        session_factory,
        *,
        review_service_factory=None,
        approval_service_factory=None,
    ):
        self.session_factory = session_factory
        self.review_service_factory = review_service_factory or (
            lambda session: LiveCanaryReviewGateService(session)
        )
        self.approval_service_factory = approval_service_factory or (
            lambda session: FullLivePolicyPromotionApprovalService(session)
        )

    def execute(
        self,
        *,
        canary_activation_id: int,
        apply: bool,
        interactive: bool = False,
        confirmation_fn: Callable[[FullLivePromotionReviewArtifact], str] | None = None,
    ):
        if (
            isinstance(canary_activation_id, bool)
            or not isinstance(canary_activation_id, int)
            or canary_activation_id < 1
        ):
            raise FullLivePolicyPromotionApprovalError(
                "canary activation ID must be positive"
            )
        with self.session_factory() as lookup_session:
            lookup_service = self.approval_service_factory(lookup_session)
            existing = lookup_service.find_existing(canary_activation_id)
            if existing is not None:
                try:
                    validated = lookup_service.validate_stored_approval(existing)
                    result = FullLivePolicyPromotionApprovalService._result(
                        canary_activation_id,
                        None,
                        ALREADY_APPROVED,
                        approval=validated.row,
                    )
                except (
                    FullLivePolicyPromotionApprovalError,
                    ValueError,
                    TypeError,
                    AttributeError,
                    KeyError,
                ) as error:
                    result = FullLivePolicyPromotionApprovalService._result(
                        canary_activation_id,
                        None,
                        INVALID_FULL_LIVE_PROMOTION_APPROVAL,
                        safe_reason=str(error),
                    )
                lookup_session.rollback()
                return result
            lookup_session.rollback()

        with self.session_factory() as review_session:
            review = self.review_service_factory(review_session).evaluate(
                canary_activation_id=canary_activation_id
            )
            if review.status != ELIGIBLE_FOR_FULL_LIVE_REVIEW:
                result = FullLivePolicyPromotionApprovalService._result(
                    canary_activation_id,
                    None,
                    REVIEW_NOT_ELIGIBLE,
                    review=review,
                    safe_reason=f"review status is {review.status}",
                )
                review_session.rollback()
                return result
            try:
                artifact = freeze_full_live_promotion_review_artifact(
                    review, canary_activation_id=canary_activation_id
                )
            except (ValueError, TypeError, AttributeError, KeyError) as error:
                review_session.rollback()
                return FullLivePolicyPromotionApprovalService._result(
                    canary_activation_id,
                    None,
                    INVALID_FULL_LIVE_PROMOTION_APPROVAL,
                    review=review,
                    safe_reason=str(error),
                )
            if not apply:
                review_session.rollback()
                return FullLivePolicyPromotionApprovalService._result(
                    canary_activation_id, artifact, DRY_RUN
                )
            if not interactive or confirmation_fn is None:
                review_session.rollback()
                return FullLivePolicyPromotionApprovalService._result(
                    canary_activation_id,
                    artifact,
                    INTERACTIVE_CONFIRMATION_REQUIRED,
                    safe_reason="interactive exact-signature confirmation is required",
                )
            try:
                confirmed = confirmation_fn(artifact)
            except EOFError, OSError:
                review_session.rollback()
                return FullLivePolicyPromotionApprovalService._result(
                    canary_activation_id,
                    artifact,
                    INTERACTIVE_CONFIRMATION_REQUIRED,
                    safe_reason="interactive confirmation input is unavailable",
                )
            if confirmed != artifact.review_decision_signature:
                review_session.rollback()
                return FullLivePolicyPromotionApprovalService._result(
                    canary_activation_id,
                    artifact,
                    CONFIRMATION_MISMATCH,
                    safe_reason="confirmation does not match the fresh frozen Review",
                )
            review_session.rollback()

        with self.session_factory() as write_session:
            result = self.approval_service_factory(write_session).approve_artifact(
                artifact=artifact,
                confirmed_review_decision_signature=confirmed,
            )
            if result.approval_status == CREATED:
                write_session.commit()
            else:
                write_session.rollback()
            return result


__all__ = [
    "ALREADY_APPROVED",
    "APPROVAL_CONFLICT",
    "APPROVAL_SCHEMA_VERSION",
    "APPROVAL_SOURCE",
    "CONFIRMATION_MISMATCH",
    "CREATED",
    "DRY_RUN",
    "FullLivePolicyPromotionApprovalError",
    "FullLivePolicyPromotionApprovalResult",
    "FullLivePolicyPromotionApprovalService",
    "FullLivePolicyPromotionApprovalWorkflow",
    "FullLivePromotionReviewArtifact",
    "INTERACTIVE_CONFIRMATION_REQUIRED",
    "INVALID_FULL_LIVE_PROMOTION_APPROVAL",
    "REPORT_TYPE",
    "REVIEW_NOT_ELIGIBLE",
    "ValidatedFullLivePolicyPromotionApproval",
    "freeze_full_live_promotion_review_artifact",
    "full_live_promotion_approval_payload",
    "full_live_promotion_approval_signature",
    "load_and_validate_full_live_policy_promotion_approval",
]
