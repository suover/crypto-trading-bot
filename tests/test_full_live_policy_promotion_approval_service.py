from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from crypto_trading_bot.services.full_live_policy_promotion_approval_service import (
    ALREADY_APPROVED,
    APPROVAL_CONFLICT,
    CONFIRMATION_MISMATCH,
    CREATED,
    DRY_RUN,
    INTERACTIVE_CONFIRMATION_REQUIRED,
    INVALID_FULL_LIVE_PROMOTION_APPROVAL,
    REVIEW_NOT_ELIGIBLE,
    FullLivePolicyPromotionApprovalError,
    FullLivePolicyPromotionApprovalService,
    FullLivePolicyPromotionApprovalWorkflow,
    freeze_full_live_promotion_review_artifact,
    full_live_promotion_approval_payload,
    full_live_promotion_approval_signature,
)
from crypto_trading_bot.services.live_canary_review_gate_service import (
    INSUFFICIENT_DATA,
    INVALID_CANARY_DATA,
    NOT_ELIGIBLE,
    NO_CANARY,
    live_canary_review_decision_signature_from_payload,
)
from tests.test_live_canary_review_gate_service import (
    NOW,
    _evaluate,
    _payload,
    _report,
)


def eligible_review():
    payload = _payload()
    payload["activation_provenance"].update(
        activation_signature="activation-signature",
        user_id=11,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="candidate-scenario",
        scenario_definition_signature="scenario-signature",
        baseline_policy_signature="baseline-signature",
        canary_policy_signature="canary-signature",
        effective_top_n=7,
    )
    payload["safety_binding_provenance"].update(
        safety_binding_id=31,
        safety_binding_signature="binding-signature",
    )
    payload["promotion_provenance"].update(
        promotion_approval_signature="promotion-signature"
    )
    return _evaluate(_report(payload=payload))


def artifact():
    return freeze_full_live_promotion_review_artifact(
        eligible_review(), canary_activation_id=7
    )


def service(*, now=NOW + timedelta(seconds=1)):
    session = MagicMock()
    value = FullLivePolicyPromotionApprovalService(session, now_fn=lambda: now)
    value._validate_current_lineage = MagicMock(return_value=(1, 2, 3, 4, None))
    value.find_existing = MagicMock(return_value=None)
    return value, session


def test_eligible_review_freezes_complete_session_independent_artifact():
    review = eligible_review()
    value = freeze_full_live_promotion_review_artifact(review)
    assert value.canary_activation_id == 7
    assert value.candidate_id == 17
    assert value.evidence_signature == review.evidence_signature
    assert value.review_decision_signature == review.review_decision_signature
    assert (
        value.review_decision_payload["evidence_signature"] == value.evidence_signature
    )
    assert all(item["status"] == "PASS" for item in value.review_checks)


def test_artifact_defensively_freezes_nested_review_data():
    review = eligible_review()
    value = freeze_full_live_promotion_review_artifact(review)
    original = value.review_policy_definition["schema_version"]
    review.review_policy_definition["schema_version"] = "mutated"
    review.evidence.payload["activation_provenance"]["exchange"] = "MUTATED"
    assert value.review_policy_definition["schema_version"] == original
    assert value.exchange == "UPBIT"
    with pytest.raises(TypeError):
        value.review_decision_payload["status"] = "mutated"


@pytest.mark.parametrize(
    "status",
    (NO_CANARY, INVALID_CANARY_DATA, INSUFFICIENT_DATA, NOT_ELIGIBLE),
)
def test_noneligible_review_status_is_rejected(status):
    review = replace(
        eligible_review(),
        status=status,
        eligible_for_full_live_review=False,
    )
    with pytest.raises(FullLivePolicyPromotionApprovalError):
        freeze_full_live_promotion_review_artifact(review)


@pytest.mark.parametrize(
    "change",
    (
        {"eligible_for_full_live_review": False},
        {"evidence_provenance_verified": False},
        {"sample_sufficiency_assessed": False},
        {"policy_decision_performed": False},
        {"statistical_inference_performed": True},
        {"failed_checks": (SimpleNamespace(status="FAIL"),)},
        {"insufficient_checks": (SimpleNamespace(status="INSUFFICIENT"),)},
        {"invalid_checks": (SimpleNamespace(status="INVALID"),)},
        {"external_calls": True},
        {"database_write": True},
        {"full_live_promotion_performed": True},
    ),
)
def test_eligible_review_internal_contradictions_are_rejected(change):
    with pytest.raises(FullLivePolicyPromotionApprovalError):
        freeze_full_live_promotion_review_artifact(replace(eligible_review(), **change))


def test_review_signature_and_policy_tampering_are_rejected():
    review = eligible_review()
    with pytest.raises(FullLivePolicyPromotionApprovalError):
        freeze_full_live_promotion_review_artifact(
            replace(
                review,
                review_decision_signature="live-canary-review-gate-v1:" + "a" * 64,
            )
        )
    with pytest.raises(FullLivePolicyPromotionApprovalError):
        freeze_full_live_promotion_review_artifact(
            replace(
                review, review_policy_signature="live-canary-review-gate-v1:" + "b" * 64
            )
        )


@pytest.mark.parametrize(
    "change",
    (
        {"candidate_id": 999},
        {"evidence_as_of": NOW - timedelta(seconds=1)},
        {"review_evaluated_at": NOW + timedelta(seconds=1)},
    ),
)
def test_signed_review_payload_must_match_all_top_level_audit_fields(change):
    value, session = service()
    frozen = replace(artifact(), **change)
    result = value.approve_artifact(
        artifact=frozen,
        confirmed_review_decision_signature=frozen.review_decision_signature,
    )
    assert result.approval_status == INVALID_FULL_LIVE_PROMOTION_APPROVAL
    session.add.assert_not_called()


def test_stored_payload_signature_helper_preserves_review_signature_semantics():
    review = eligible_review()
    frozen = artifact()
    assert (
        live_canary_review_decision_signature_from_payload(
            dict(frozen.review_decision_payload)
        )
        == review.review_decision_signature
    )


def test_correct_exact_confirmation_creates_only_approval_row():
    value, session = service()
    frozen = artifact()
    result = value.approve_artifact(
        artifact=frozen,
        confirmed_review_decision_signature=frozen.review_decision_signature,
    )
    assert result.approval_status == CREATED
    assert result.database_write is True
    assert result.human_approval_recorded is True
    assert result.full_live_promotion_approval_persisted is True
    assert result.full_live_policy_activated is False
    assert result.external_calls is False
    assert result.live_policy_change is result.live_order_change is False
    assert result.ranking_runtime_changed is result.canary_state_changed is False
    session.add.assert_called_once_with(result.approval)
    session.flush.assert_called_once()
    value._validate_current_lineage.assert_called_once_with(frozen)


@pytest.mark.parametrize(
    "confirmation",
    (
        "live-canary-review-gate-v1:" + "0" * 64,
        " live-canary-review-gate-v1:" + "0" * 64,
        "LIVE-CANARY-REVIEW-GATE-V1:" + "0" * 64,
        "yes",
    ),
)
def test_wrong_or_noncanonical_confirmation_writes_nothing(confirmation):
    value, session = service()
    result = value.approve_artifact(
        artifact=artifact(), confirmed_review_decision_signature=confirmation
    )
    assert result.approval_status == CONFIRMATION_MISMATCH
    assert result.database_write is False
    session.add.assert_not_called()
    value._validate_current_lineage.assert_not_called()


def test_human_approval_clock_is_captured_after_confirmation_and_must_not_regress():
    value, session = service(now=NOW - timedelta(seconds=1))
    frozen = artifact()
    result = value.approve_artifact(
        artifact=frozen,
        confirmed_review_decision_signature=frozen.review_decision_signature,
    )
    assert result.approval_status == INVALID_FULL_LIVE_PROMOTION_APPROVAL
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "field",
    (
        "canary_activation_signature",
        "safety_binding_signature",
        "promotion_approval_signature",
        "candidate_id",
        "user_id",
        "exchange",
        "quote_asset",
        "scenario_name",
        "termination_signature",
    ),
)
def test_phase_two_lineage_corruption_fails_closed(field):
    value, session = service()
    frozen = artifact()
    value._validate_current_lineage.side_effect = FullLivePolicyPromotionApprovalError(
        f"{field} mismatch"
    )
    result = value.approve_artifact(
        artifact=frozen,
        confirmed_review_decision_signature=frozen.review_decision_signature,
    )
    assert result.approval_status == INVALID_FULL_LIVE_PROMOTION_APPROVAL
    session.add.assert_not_called()


def test_approval_signature_is_canonical_and_covers_required_provenance():
    value, _ = service()
    frozen = artifact()
    row = value._build_approval(frozen, NOW + timedelta(seconds=1))
    row.approval_signature = full_live_promotion_approval_signature(row)
    assert full_live_promotion_approval_signature(row) == row.approval_signature
    assert (
        full_live_promotion_approval_payload(row)["evidence_signature"]
        == frozen.evidence_signature
    )
    variants = (
        replace(frozen, candidate_id=18),
        replace(frozen, canary_activation_signature="changed"),
        replace(frozen, safety_binding_signature="changed"),
        replace(frozen, evidence_signature="live-canary-evidence-v1:" + "0" * 64),
        replace(
            frozen, review_decision_signature="live-canary-review-gate-v1:" + "0" * 64
        ),
    )
    assert all(
        full_live_promotion_approval_signature(
            value._build_approval(item, NOW + timedelta(seconds=1))
        )
        != row.approval_signature
        for item in variants
    )
    later = value._build_approval(frozen, NOW + timedelta(seconds=2))
    assert full_live_promotion_approval_signature(later) != row.approval_signature


def test_equivalent_decimal_and_timezone_values_have_same_approval_signature():
    value, _ = service()
    frozen = artifact()
    first = value._build_approval(frozen, NOW + timedelta(seconds=1))
    second = value._build_approval(frozen, (NOW + timedelta(seconds=1)).astimezone())
    first.extra_decimal = Decimal("1.0")
    second.extra_decimal = Decimal("1.00")
    assert full_live_promotion_approval_signature(
        first
    ) == full_live_promotion_approval_signature(second)


def test_existing_same_artifact_is_already_approved_and_different_is_conflict():
    value, _ = service()
    frozen = artifact()
    existing = value._build_approval(frozen, NOW + timedelta(seconds=1))
    existing.approval_signature = full_live_promotion_approval_signature(existing)
    value.find_existing.return_value = existing
    value.validate_stored_approval = MagicMock(
        return_value=SimpleNamespace(row=existing)
    )
    same = value.approve_artifact(
        artifact=frozen,
        confirmed_review_decision_signature=frozen.review_decision_signature,
    )
    assert same.approval_status == ALREADY_APPROVED
    different = replace(
        frozen,
        review_decision_signature="live-canary-review-gate-v1:" + "0" * 64,
    )
    value._validate_artifact = MagicMock()
    conflict = value.approve_artifact(
        artifact=different,
        confirmed_review_decision_signature=different.review_decision_signature,
    )
    assert conflict.approval_status == APPROVAL_CONFLICT


@pytest.mark.parametrize(
    ("concurrent_signature", "expected_status"),
    (
        ("same", ALREADY_APPROVED),
        ("different", APPROVAL_CONFLICT),
    ),
)
def test_concurrent_unique_race_distinguishes_same_and_different_artifacts(
    concurrent_signature, expected_status
):
    value, session = service()
    frozen = artifact()
    existing = value._build_approval(frozen, NOW + timedelta(seconds=1))
    if concurrent_signature == "different":
        existing.review_decision_signature = "live-canary-review-gate-v1:" + ("0" * 64)
    existing.approval_signature = full_live_promotion_approval_signature(existing)
    value.find_existing.side_effect = (None, existing)
    value.validate_stored_approval = MagicMock(
        return_value=SimpleNamespace(row=existing)
    )
    session.flush.side_effect = IntegrityError("insert", {}, Exception("unique"))

    result = value.approve_artifact(
        artifact=frozen,
        confirmed_review_decision_signature=frozen.review_decision_signature,
    )

    assert result.approval_status == expected_status
    assert result.full_live_promotion_approval_created is False
    assert result.database_write is False


class SessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        session = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = session
        context.__exit__.return_value = False
        self.sessions.append(session)
        return context


def workflow(review, *, write_result=None):
    sessions = SessionFactory()
    review_service = MagicMock()
    review_service.evaluate.return_value = review
    lookup_service = MagicMock()
    lookup_service.find_existing.return_value = None
    write_service = MagicMock()
    write_service.approve_artifact.return_value = write_result
    services = iter((lookup_service, write_service))
    value = FullLivePolicyPromotionApprovalWorkflow(
        sessions,
        review_service_factory=lambda _session: review_service,
        approval_service_factory=lambda _session: next(services),
    )
    return value, sessions, review_service, lookup_service, write_service


def test_preview_is_read_only_and_signature_is_informational():
    value, sessions, review_service, _, _ = workflow(eligible_review())
    result = value.execute(canary_activation_id=7, apply=False)
    assert result.approval_status == DRY_RUN
    assert result.database_write is False
    assert result.confirmation_required is True
    assert review_service.evaluate.call_count == 1
    assert len(sessions.sessions) == 2
    assert all(session.commit.call_count == 0 for session in sessions.sessions)


def test_apply_evaluates_review_exactly_once_and_uses_separate_write_session():
    frozen = artifact()
    created = FullLivePolicyPromotionApprovalService._result(
        7, frozen, CREATED, approval=SimpleNamespace(candidate_id=17), created=True
    )
    value, sessions, review_service, _, write_service = workflow(
        eligible_review(), write_result=created
    )
    confirmations = []
    result = value.execute(
        canary_activation_id=7,
        apply=True,
        interactive=True,
        confirmation_fn=lambda item: (
            confirmations.append(item.review_decision_signature)
            or item.review_decision_signature
        ),
    )
    assert result.approval_status == CREATED
    assert review_service.evaluate.call_count == 1
    assert confirmations == [eligible_review().review_decision_signature]
    assert len(sessions.sessions) == 3
    sessions.sessions[1].rollback.assert_called_once()
    sessions.sessions[2].commit.assert_called_once()
    write_service.approve_artifact.assert_called_once()


def test_apply_accepts_fresh_bbb_without_comparing_preview_aaa():
    preview = eligible_review()
    apply_review = _evaluate(
        _report(payload=deepcopy(preview.evidence.payload)),
        now=NOW + timedelta(seconds=1),
    )
    assert preview.review_decision_signature != apply_review.review_decision_signature
    frozen = freeze_full_live_promotion_review_artifact(apply_review)
    created = FullLivePolicyPromotionApprovalService._result(
        7, frozen, CREATED, approval=SimpleNamespace(candidate_id=17), created=True
    )
    value, _, review_service, _, _ = workflow(apply_review, write_result=created)
    result = value.execute(
        canary_activation_id=7,
        apply=True,
        interactive=True,
        confirmation_fn=lambda item: item.review_decision_signature,
    )
    assert result.approval_status == CREATED
    assert review_service.evaluate.call_count == 1


def test_previous_preview_signature_is_rejected_without_write_phase():
    previous = eligible_review()
    current = _evaluate(
        _report(payload=deepcopy(previous.evidence.payload)),
        now=NOW + timedelta(seconds=1),
    )
    value, sessions, review_service, _, write_service = workflow(current)
    result = value.execute(
        canary_activation_id=7,
        apply=True,
        interactive=True,
        confirmation_fn=lambda _item: previous.review_decision_signature,
    )
    assert result.approval_status == CONFIRMATION_MISMATCH
    assert review_service.evaluate.call_count == 1
    assert len(sessions.sessions) == 2
    write_service.approve_artifact.assert_not_called()


@pytest.mark.parametrize("interactive", (False, True))
def test_noninteractive_or_unavailable_input_fails_closed(interactive):
    value, sessions, _, _, write_service = workflow(eligible_review())
    confirmation = (
        (lambda _item: (_ for _ in ()).throw(EOFError())) if interactive else None
    )
    result = value.execute(
        canary_activation_id=7,
        apply=True,
        interactive=interactive,
        confirmation_fn=confirmation,
    )
    assert result.approval_status == INTERACTIVE_CONFIRMATION_REQUIRED
    assert len(sessions.sessions) == 2
    write_service.approve_artifact.assert_not_called()


@pytest.mark.parametrize(
    "status",
    (NO_CANARY, INVALID_CANARY_DATA, INSUFFICIENT_DATA, NOT_ELIGIBLE),
)
def test_workflow_does_not_prompt_or_write_for_ineligible_review(status):
    review = replace(
        eligible_review(), status=status, eligible_for_full_live_review=False
    )
    value, sessions, _, _, write_service = workflow(review)
    prompt = MagicMock()
    result = value.execute(
        canary_activation_id=7,
        apply=True,
        interactive=True,
        confirmation_fn=prompt,
    )
    assert result.approval_status == REVIEW_NOT_ELIGIBLE
    assert len(sessions.sessions) == 2
    prompt.assert_not_called()
    write_service.approve_artifact.assert_not_called()


def test_valid_existing_approval_skips_fresh_review():
    review_service = MagicMock()
    existing = SimpleNamespace(candidate_id=17)
    validated = SimpleNamespace(row=existing)
    lookup = MagicMock()
    lookup.find_existing.return_value = existing
    lookup.validate_stored_approval.return_value = validated
    sessions = SessionFactory()
    value = FullLivePolicyPromotionApprovalWorkflow(
        sessions,
        review_service_factory=lambda _session: review_service,
        approval_service_factory=lambda _session: lookup,
    )
    result = value.execute(canary_activation_id=7, apply=True)
    assert result.approval_status == ALREADY_APPROVED
    review_service.evaluate.assert_not_called()
    assert len(sessions.sessions) == 1


def test_corrupt_existing_approval_is_not_silently_already_approved():
    lookup = MagicMock()
    lookup.find_existing.return_value = SimpleNamespace(candidate_id=17)
    lookup.validate_stored_approval.side_effect = FullLivePolicyPromotionApprovalError(
        "corrupt"
    )
    sessions = SessionFactory()
    value = FullLivePolicyPromotionApprovalWorkflow(
        sessions, approval_service_factory=lambda _session: lookup
    )
    result = value.execute(canary_activation_id=7, apply=False)
    assert result.approval_status == INVALID_FULL_LIVE_PROMOTION_APPROVAL
