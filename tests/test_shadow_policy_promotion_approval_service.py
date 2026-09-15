from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_REVIEW_DATA,
    NOT_ELIGIBLE,
    NO_SHADOW_ENROLLMENT,
    PASS,
    RESULT_TYPE as REVIEW_RESULT_TYPE,
    SHADOW_REVIEW_GATE_V1,
    ShadowReviewGateCheckResult,
    shadow_review_policy_definition,
    shadow_review_policy_signature,
)
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ALREADY_APPROVED,
    CREATED,
    DRY_RUN,
    INVALID_PROMOTION_APPROVAL,
    REVIEW_DECISION_CHANGED,
    REVIEW_NOT_ELIGIBLE,
    ShadowPolicyPromotionApprovalService,
    promotion_approval_signature,
)


NOW = datetime(2026, 9, 14, tzinfo=UTC)


def enrollment():
    return SimpleNamespace(
        id=7,
        candidate_id=9,
        candidate_schema_version="research-policy-candidate-v1",
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="shadow-policy",
        scenario_definition_signature="scenario-definition",
        component_weights={"momentum": "1.0"},
        dataset_schema_version="strategy-replay-v1",
        baseline_policy_signature="baseline-policy",
        effective_top_n=7,
        shadow_enrolled_at=NOW - timedelta(days=20),
        shadow_snapshot_id_watermark=10,
        shadow_captured_at_watermark=NOW - timedelta(days=20),
        gate_decision_signature="pre-shadow-decision",
    )


def evidence():
    ids = list(range(1, 43))
    return {
        "timeline_snapshot_ids": ids,
        "candidate_context_snapshot_ids": ids,
        "successful_selection_snapshot_ids": ids,
        "gross_successful_snapshot_ids_by_horizon": [
            {"horizon_minutes": horizon, "snapshot_ids": ids}
            for horizon in (60, 240, 1440)
        ],
        "turnover_transitions": [
            {"previous_snapshot_id": value, "current_snapshot_id": value + 1}
            for value in range(1, 42)
        ],
        "cost_adjustable_snapshot_ids_by_horizon": [
            {"horizon_minutes": horizon, "snapshot_ids": ids[1:]}
            for horizon in (60, 240, 1440)
        ],
    }


def review_payload(checks):
    return {
        "result_type": REVIEW_RESULT_TYPE,
        "candidate": {
            "candidate_id": 9,
            "shadow_enrollment_id": 7,
            "user_id": 3,
            "exchange": "UPBIT",
            "quote_asset": "KRW",
            "scenario_name": "shadow-policy",
            "scenario_definition_signature": "scenario-definition",
            "baseline_policy_signature": "baseline-policy",
            "effective_top_n": 7,
        },
        "stored_pre_shadow_gate_decision_signature": "pre-shadow-decision",
        "review_policy_signature": shadow_review_policy_signature(
            SHADOW_REVIEW_GATE_V1
        ),
        "review_policy_definition": shadow_review_policy_definition(
            SHADOW_REVIEW_GATE_V1
        ),
        "performance_evidence_as_of": NOW.isoformat(),
        "shadow_evaluation_snapshot_id_ceiling": 42,
        "successful_selection_snapshot_ids": list(range(1, 43)),
        "gross": [],
        "turnover": {},
        "cost": [],
        "checks": checks,
        "status": ELIGIBLE_FOR_PROMOTION_REVIEW,
        "review_evaluated_at": NOW.isoformat(),
    }


def payload_signature(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"shadow-review-gate-v1:{sha256(encoded).hexdigest()}"


def different_signature(value: str) -> str:
    return value[:-1] + ("0" if value[-1] != "0" else "1")


def eligible_review():
    check = ShadowReviewGateCheckResult(
        check_id="all",
        category="PERFORMANCE",
        status=PASS,
        horizon_minutes=None,
        observed_value="1",
        comparator=">=",
        threshold_value="1",
        reason=None,
    )
    checks = [
        {
            "check_id": "all",
            "category": "PERFORMANCE",
            "status": PASS,
            "horizon_minutes": None,
            "observed_value": "1",
            "comparator": ">=",
            "threshold_value": "1",
            "reason": None,
        }
    ]
    payload = review_payload(checks)
    signature = payload_signature(payload)
    row = enrollment()
    return SimpleNamespace(
        result_type=REVIEW_RESULT_TYPE,
        candidate_id=9,
        enrollment=row,
        review_policy_schema_version=SHADOW_REVIEW_GATE_V1.schema_version,
        review_policy=SHADOW_REVIEW_GATE_V1,
        review_policy_signature=shadow_review_policy_signature(SHADOW_REVIEW_GATE_V1),
        review_policy_definition=shadow_review_policy_definition(SHADOW_REVIEW_GATE_V1),
        evaluated_at=NOW,
        status=ELIGIBLE_FOR_PROMOTION_REVIEW,
        safe_reason=None,
        eligible_for_promotion_review=True,
        passed_checks=(check,),
        insufficient_checks=(),
        failed_checks=(),
        invalid_checks=(),
        all_checks=(check,),
        performance=SimpleNamespace(
            candidate_id=9,
            enrollment=row,
            performance_evidence_as_of=NOW,
            shadow_evaluation_snapshot_id_ceiling=42,
        ),
        pre_shadow_gate_provenance_verified=True,
        shadow_performance_provenance_verified=True,
        review_decision_signature=signature,
        sample_sufficiency_assessed=True,
        statistical_inference_performed=False,
        policy_decision_performed=True,
        promotion_performed=False,
        database_write=False,
        external_calls=False,
        live_policy_change=False,
        shadow_runtime_changed=False,
        _payload=payload,
    )


def service(monkeypatch, review):
    review_service = MagicMock()
    review_service.evaluate.return_value = review
    session = MagicMock()
    value = ShadowPolicyPromotionApprovalService(
        session, review_service=review_service, now_fn=lambda: NOW
    )
    value._find_existing = MagicMock(return_value=None)
    if hasattr(review, "_payload"):
        monkeypatch.setattr(
            "crypto_trading_bot.services.shadow_policy_promotion_approval_service.shadow_review_decision_payload",
            lambda *_: review._payload,
        )
        monkeypatch.setattr(
            "crypto_trading_bot.services.shadow_policy_promotion_approval_service.shadow_review_decision_signature",
            lambda *_: review.review_decision_signature,
        )
        monkeypatch.setattr(
            "crypto_trading_bot.services.shadow_policy_promotion_approval_service.shadow_review_evidence_provenance",
            lambda *_: evidence(),
        )
    return value, review_service, session


def test_preview_eligible_is_read_only_and_calls_review_once(monkeypatch) -> None:
    value, review_service, session = service(monkeypatch, eligible_review())
    result = value.preview(candidate_id=9)
    assert result.approval_status == DRY_RUN
    assert result.current_review_decision_signature
    assert result.database_write is False
    assert result.human_approval_recorded is False
    review_service.evaluate.assert_called_once_with(candidate_id=9)
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("review_status", "approval_status"),
    (
        (NO_SHADOW_ENROLLMENT, NO_SHADOW_ENROLLMENT),
        (INSUFFICIENT_DATA, REVIEW_NOT_ELIGIBLE),
        (NOT_ELIGIBLE, REVIEW_NOT_ELIGIBLE),
        (INVALID_REVIEW_DATA, INVALID_PROMOTION_APPROVAL),
    ),
)
def test_non_eligible_review_never_writes(monkeypatch, review_status, approval_status):
    review = eligible_review()
    review.status = review_status
    review.eligible_for_promotion_review = False
    if review_status == NO_SHADOW_ENROLLMENT:
        review.enrollment = None
    value, _, session = service(monkeypatch, review)
    result = value.preview(candidate_id=9)
    assert result.approval_status == approval_status
    assert result.database_write is False
    session.add.assert_not_called()


def test_apply_requires_exact_current_signature(monkeypatch) -> None:
    review = eligible_review()
    value, review_service, session = service(monkeypatch, review)
    changed = different_signature(review.review_decision_signature)
    result = value.approve(candidate_id=9, expected_review_decision_signature=changed)
    assert result.approval_status == REVIEW_DECISION_CHANGED
    assert result.database_write is False
    review_service.evaluate.assert_called_once()
    session.add.assert_not_called()


@pytest.mark.parametrize("review_status", (INSUFFICIENT_DATA, NOT_ELIGIBLE))
def test_apply_stops_when_review_is_no_longer_eligible(
    monkeypatch, review_status
) -> None:
    review = eligible_review()
    previously_previewed_signature = review.review_decision_signature
    review.status = review_status
    review.eligible_for_promotion_review = False
    review._payload = deepcopy(review._payload)
    review._payload["status"] = review_status
    review.review_decision_signature = payload_signature(review._payload)
    value, review_service, session = service(monkeypatch, review)

    result = value.approve(
        candidate_id=9,
        expected_review_decision_signature=previously_previewed_signature,
    )

    assert result.approval_status == REVIEW_NOT_ELIGIBLE
    assert result.database_write is False
    review_service.evaluate.assert_called_once_with(candidate_id=9)
    session.add.assert_not_called()


def test_apply_exact_signature_creates_only_approval(monkeypatch) -> None:
    review = eligible_review()
    value, review_service, session = service(monkeypatch, review)
    result = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    )
    assert result.approval_status == CREATED
    assert result.database_write is True
    assert result.human_approval_recorded is True
    assert result.promotion_approval_created is True
    assert result.live_policy_change is False
    assert result.live_order_change is False
    assert result.ranking_runtime_changed is False
    review_service.evaluate.assert_called_once()
    session.add.assert_called_once_with(result.approval)
    session.flush.assert_called_once_with()


def test_preview_a_apply_b_race_is_no_write(monkeypatch) -> None:
    first = eligible_review()
    second = eligible_review()
    second._payload = deepcopy(second._payload)
    second._payload["shadow_evaluation_snapshot_id_ceiling"] = 43
    second.review_decision_signature = payload_signature(second._payload)
    value, review_service, session = service(monkeypatch, second)
    result = value.approve(
        candidate_id=9,
        expected_review_decision_signature=first.review_decision_signature,
    )
    assert result.approval_status == REVIEW_DECISION_CHANGED
    assert result.current_review_decision_signature == second.review_decision_signature
    review_service.evaluate.assert_called_once()
    session.add.assert_not_called()


@pytest.mark.parametrize("value", ("", " bad", "bad", "shadow-review-gate-v1:xyz"))
def test_apply_rejects_invalid_expected_signature(value) -> None:
    with pytest.raises(ReplayInputError):
        ShadowPolicyPromotionApprovalService(MagicMock()).approve(
            candidate_id=9, expected_review_decision_signature=value
        )


def test_existing_approval_is_idempotent_without_review(monkeypatch) -> None:
    review = eligible_review()
    value, review_service, _ = service(monkeypatch, review)
    created = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    )
    approval = created.approval
    validated = SimpleNamespace(
        row=enrollment(),
        candidate=SimpleNamespace(
            candidate_id=9,
            candidate_schema_version="research-policy-candidate-v1",
            user_id=3,
            exchange="UPBIT",
            quote_asset="KRW",
            scenario_name="shadow-policy",
            scenario_definition_signature="scenario-definition",
            component_weights={"momentum": "1.0"},
            dataset_schema_version="strategy-replay-v1",
            baseline_policy_signature="baseline-policy",
            effective_top_n=7,
        ),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_promotion_approval_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: validated,
    )
    value._find_existing.return_value = approval
    review_service.reset_mock()
    result = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    )
    assert result.approval_status == ALREADY_APPROVED
    assert result.database_write is False
    assert result.human_approval_recorded is True
    review_service.evaluate.assert_not_called()


def test_existing_approval_different_expected_signature_is_conflict(monkeypatch):
    review = eligible_review()
    value, review_service, _ = service(monkeypatch, review)
    created = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    )
    validated = SimpleNamespace(
        row=enrollment(),
        candidate=SimpleNamespace(
            candidate_id=9,
            candidate_schema_version="research-policy-candidate-v1",
            user_id=3,
            exchange="UPBIT",
            quote_asset="KRW",
            scenario_name="shadow-policy",
            scenario_definition_signature="scenario-definition",
            component_weights={"momentum": "1.0"},
            dataset_schema_version="strategy-replay-v1",
            baseline_policy_signature="baseline-policy",
            effective_top_n=7,
        ),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_promotion_approval_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: validated,
    )
    value._find_existing.return_value = created.approval
    review_service.reset_mock()
    different = different_signature(review.review_decision_signature)
    result = value.approve(candidate_id=9, expected_review_decision_signature=different)
    assert result.approval_status == REVIEW_DECISION_CHANGED
    review_service.evaluate.assert_not_called()


def test_approval_signature_is_canonical_and_covers_semantics(monkeypatch) -> None:
    review = eligible_review()
    value, _, _ = service(monkeypatch, review)
    approval = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    ).approval
    first = promotion_approval_signature(approval)
    approval.human_approved_at = approval.human_approved_at.astimezone(
        timezone(timedelta(hours=9))
    )
    assert promotion_approval_signature(approval) == first
    approval.review_decision_signature = different_signature(
        approval.review_decision_signature
    )
    assert promotion_approval_signature(approval) != first


def test_approval_signature_canonicalizes_decimal_representation(monkeypatch) -> None:
    review = eligible_review()
    value, _, _ = service(monkeypatch, review)
    approval = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    ).approval
    approval.component_weights = {"momentum": Decimal("1.00")}
    first = promotion_approval_signature(approval)
    approval.component_weights = {"momentum": Decimal("1")}
    assert promotion_approval_signature(approval) == first


@pytest.mark.parametrize(
    "mutate",
    (
        lambda row: setattr(row, "candidate_id", row.candidate_id + 1),
        lambda row: setattr(row, "shadow_enrollment_id", row.shadow_enrollment_id + 1),
        lambda row: setattr(
            row,
            "review_decision_signature",
            different_signature(row.review_decision_signature),
        ),
        lambda row: row.review_checks[0].__setitem__("observed_value", "2"),
        lambda row: row.review_evidence_provenance["timeline_snapshot_ids"].append(43),
        lambda row: setattr(
            row, "human_approved_at", row.human_approved_at + timedelta(seconds=1)
        ),
    ),
)
def test_approval_signature_changes_with_signed_semantics(monkeypatch, mutate) -> None:
    review = eligible_review()
    value, _, _ = service(monkeypatch, review)
    approval = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    ).approval
    first = promotion_approval_signature(approval)
    mutate(approval)
    assert promotion_approval_signature(approval) != first


@pytest.mark.parametrize(
    "mutate",
    (
        lambda row: setattr(row, "candidate_id", 10),
        lambda row: setattr(row, "shadow_enrollment_id", 8),
        lambda row: setattr(row, "component_weights", {"momentum": "0"}),
        lambda row: setattr(row, "review_policy_signature", "corrupt"),
        lambda row: row.review_decision_payload.__setitem__("status", "corrupt"),
        lambda row: setattr(row, "review_decision_signature", "corrupt"),
        lambda row: setattr(row, "approval_source", "AUTOMATIC"),
        lambda row: setattr(
            row, "human_approved_at", row.review_evaluated_at - timedelta(seconds=1)
        ),
        lambda row: setattr(row, "approval_signature", "corrupt"),
    ),
)
def test_existing_approval_corruption_is_invalid(monkeypatch, mutate) -> None:
    review = eligible_review()
    value, review_service, _ = service(monkeypatch, review)
    approval = value.approve(
        candidate_id=9,
        expected_review_decision_signature=review.review_decision_signature,
    ).approval
    mutate(approval)
    validated = SimpleNamespace(
        row=enrollment(),
        candidate=SimpleNamespace(
            candidate_id=9,
            candidate_schema_version="research-policy-candidate-v1",
            user_id=3,
            exchange="UPBIT",
            quote_asset="KRW",
            scenario_name="shadow-policy",
            scenario_definition_signature="scenario-definition",
            component_weights={"momentum": "1.0"},
            dataset_schema_version="strategy-replay-v1",
            baseline_policy_signature="baseline-policy",
            effective_top_n=7,
        ),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_promotion_approval_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: validated,
    )
    value._find_existing.return_value = approval
    review_service.reset_mock()
    result = value.preview(candidate_id=9)
    assert result.approval_status == INVALID_PROMOTION_APPROVAL
    assert result.database_write is False
    review_service.evaluate.assert_not_called()
