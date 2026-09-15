from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.live_canary_evidence_service import (
    ACTIVE,
    EVIDENCE_AVAILABLE,
    EXHAUSTED,
    EXPIRED,
    INVALID_CANARY_EVIDENCE,
    NO_CANARY_ACTIVATION,
    NO_CANARY_RUNS,
    STOPPED,
    LiveCanaryEvidenceReport,
    live_canary_evidence_signature,
)
from crypto_trading_bot.services.live_canary_review_gate_service import (
    ELIGIBLE_FOR_FULL_LIVE_REVIEW,
    FAIL,
    INSUFFICIENT_DATA,
    INVALID_CANARY_DATA,
    LIVE_CANARY_REVIEW_GATE_V1,
    NOT_ELIGIBLE,
    NO_CANARY,
    LiveCanaryReviewGateError,
    LiveCanaryReviewGateService,
    live_canary_review_decision_signature,
    live_canary_review_policy_definition,
    live_canary_review_policy_signature,
    validate_live_canary_review_policy,
)
from scripts.evaluate_live_canary_review_gate import parse_arguments, report


NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
STARTED = NOW - timedelta(hours=40)


class FakeEvidenceService:
    def __init__(self, value, events=None):
        self.value = value
        self.calls = []
        self.events = events

    def evaluate(self, *, canary_activation_id):
        self.calls.append(canary_activation_id)
        if self.events is not None:
            self.events.append("evidence")
        return self.value


def _payload():
    runs = [
        {
            "canary_run_id": index + 1,
            "run_ordinal": index + 1,
            "analysis_run_id": index + 11,
            "pipeline_run_id": f"pipeline-{index + 1}",
            "reserved_at": STARTED + timedelta(hours=index * 8),
            "market_universe_analysis_status": "SUCCESS",
        }
        for index in range(6)
    ]
    recommendations = [
        {
            "recommendation_id": index + 101,
            "pipeline_run_id": f"pipeline-{index + 1}",
            "action": "BUY" if index == 0 else "HOLD",
            "provenance_mode": "CANARY_RECOMMENDATION",
            "provenance_valid": True,
        }
        for index in range(6)
    ]
    return {
        "activation_provenance": {
            "canary_activation_id": 7,
            "candidate_id": 17,
            "started_at": STARTED,
            "expires_at": STARTED + timedelta(hours=48),
            "max_analysis_runs": 6,
        },
        "safety_binding_provenance": {
            "max_buy_order_amount_krw": Decimal("10000"),
            "daily_max_buy_amount_krw": Decimal("30000"),
        },
        "promotion_provenance": {"promotion_approval_id": 27},
        "termination_provenance": None,
        "runs": runs,
        "run_summary": {
            "reserved_run_count": 6,
            "successful_market_universe_run_count": 6,
            "failed_market_universe_run_count": 0,
            "unfinished_market_universe_run_count": 0,
        },
        "selection_evidence": {},
        "recommendation_evidence": {"recommendations": recommendations},
        "approval_evidence": {
            "approval_requests": [
                {
                    "approval_request_id": 201,
                    "recommendation_id": 101,
                    "status": "APPROVED",
                }
            ]
        },
        "order_evidence": {
            "orders": [
                {
                    "order_log_id": 301,
                    "recommendation_id": 101,
                    "approval_request_id": 201,
                    "side": "BUY",
                    "amount_krw": Decimal("9000"),
                    "status": "LIVE_DONE",
                    "created_at": STARTED + timedelta(hours=1),
                    "executed_quantity": Decimal("0.0001"),
                    "executed_funds_krw": Decimal("9000"),
                    "preflight_failed_before_canary_audit": False,
                }
            ],
            "summary": {
                "live_failed_count": 0,
                "live_unknown_count": 0,
                "pending_order_count": 0,
            },
        },
        "fill_evidence": {"fills": [{"order_log_id": 301}]},
        "operational_evidence": {
            "alerts": [
                {
                    "alert_type": "LIVE_CANARY_STARTED",
                    "error_code": "CANARY_STARTED",
                    "delivery_status": "SENT",
                    "resolved_at": None,
                }
            ],
            "summary": {
                "canary_started_alert_count": 1,
                "invalid_provenance_alert_count": 0,
                "pipeline_failure_alert_count": 0,
                "alert_delivery_failed_count": 0,
                "legacy_unstructured_canary_alert_count": 0,
            },
        },
        "recommendation_outcome_evidence": {
            "outcomes": [],
            "outcome_is_actual_execution_pnl": False,
        },
        "direct_canary_financial_facts": {
            "execution_net_cash_flow_krw": Decimal("-9004.5"),
            "canary_realized_pnl_calculated": False,
            "execution_net_cash_flow_is_profit": False,
        },
        "portfolio_context": {
            "account_portfolio_value_delta_krw": Decimal("-100000"),
            "portfolio_delta_is_canary_pnl": False,
        },
        "account_bot_pnl_context": {
            "available": True,
            "canary_attributed": False,
            "recognized_realized_pnl_krw": Decimal("-50000"),
        },
        "integrity": {
            "activation_provenance_verified": True,
            "safety_binding_verified": True,
            "promotion_provenance_verified": True,
            "termination_provenance_verified": True,
            "all_canary_runs_verified": True,
            "all_recommendation_lineages_verified": True,
            "all_canary_order_lineages_verified": True,
            "order_fill_integrity_verified": True,
            "snapshot_consistency_verified": True,
            "findings": [],
        },
        "safety_flags": {
            "database_write": False,
            "external_calls": False,
            "live_policy_change": False,
            "live_order_change": False,
            "ranking_runtime_changed": False,
            "canary_state_changed": False,
            "promotion_performed": False,
            "full_live_promotion_performed": False,
            "sample_sufficiency_assessed": False,
            "policy_decision_performed": False,
            "statistical_inference_performed": False,
        },
    }


def _report(
    *,
    status=EVIDENCE_AVAILABLE,
    lifecycle=EXHAUSTED,
    payload=None,
    activation_id=7,
):
    value = LiveCanaryEvidenceReport(
        status=status,
        lifecycle_state=lifecycle,
        canary_activation_id=activation_id,
        candidate_id=17 if status != NO_CANARY_ACTIVATION else None,
        evidence_as_of=NOW,
        snapshot_consistency="REPEATABLE_READ_READ_ONLY",
        ceilings={"order_log_id_ceiling": 301},
        payload=deepcopy(_payload() if payload is None else payload),
        evidence_signature="",
    )
    unsigned = value.as_dict()
    unsigned.pop("evidence_signature")
    return replace(value, evidence_signature=live_canary_evidence_signature(unsigned))


def _evaluate(value, *, policy=LIVE_CANARY_REVIEW_GATE_V1, now=NOW):
    source = FakeEvidenceService(value)
    result = LiveCanaryReviewGateService(
        MagicMock(), policy=policy, evidence_service=source, now_fn=lambda: now
    ).evaluate(canary_activation_id=7)
    assert source.calls == [7]
    return result


def _resign(value, payload):
    return _report(
        status=value.status,
        lifecycle=value.lifecycle_state,
        payload=payload,
        activation_id=value.canary_activation_id,
    )


def _check(result, check_id):
    return next(item for item in result.all_checks if item.check_id == check_id)


def test_full_valid_terminal_evidence_is_eligible_despite_negative_financial_context():
    result = _evaluate(_report())
    assert result.status == ELIGIBLE_FOR_FULL_LIVE_REVIEW
    assert result.eligible_for_full_live_review is True
    assert result.review_decision_signature.startswith("live-canary-review-gate-v1:")
    assert all(item.status == "PASS" for item in result.all_checks)
    assert result.database_write is result.external_calls is False
    assert result.promotion_performed is result.full_live_promotion_performed is False


def test_evidence_is_evaluated_before_review_clock_and_no_session_query_is_made():
    events = []
    source = FakeEvidenceService(_report(), events)
    session = MagicMock()
    result = LiveCanaryReviewGateService(
        session,
        evidence_service=source,
        now_fn=lambda: events.append("clock") or NOW,
    ).evaluate(canary_activation_id=7)
    assert events == ["evidence", "clock"]
    assert result.status == ELIGIBLE_FOR_FULL_LIVE_REVIEW
    session.assert_not_called()


def test_no_canary_has_no_policy_decision_or_signature():
    result = _evaluate(_report(status=NO_CANARY_ACTIVATION, lifecycle=None))
    assert result.status == NO_CANARY
    assert result.review_decision_signature is None
    assert result.sample_sufficiency_assessed is False
    assert result.policy_decision_performed is False


def test_structurally_invalid_evidence_is_invalid_without_policy_decision():
    result = _evaluate(_report(status=INVALID_CANARY_EVIDENCE))
    assert result.status == INVALID_CANARY_DATA
    assert result.invalid_checks
    assert result.review_decision_signature is None
    assert result.policy_decision_performed is False


def test_evidence_signature_mismatch_fails_closed():
    value = replace(_report(), evidence_signature="live-canary-evidence-v1:bad")
    result = _evaluate(value)
    assert result.status == INVALID_CANARY_DATA
    assert "signature" in result.invalid_checks[0].reason


def test_malformed_evidence_object_returns_invalid_instead_of_raising():
    malformed = SimpleNamespace(
        status=EVIDENCE_AVAILABLE,
        candidate_id=None,
        evidence_signature=None,
        evidence_as_of=None,
        as_dict=lambda: (_ for _ in ()).throw(TypeError("malformed")),
    )
    result = _evaluate(malformed)
    assert result.status == INVALID_CANARY_DATA
    assert result.policy_decision_performed is False


def test_no_canary_runs_is_insufficient():
    payload = _payload()
    payload["runs"] = []
    payload["run_summary"].update(
        reserved_run_count=0,
        successful_market_universe_run_count=0,
        failed_market_universe_run_count=0,
        unfinished_market_universe_run_count=0,
    )
    payload["recommendation_evidence"]["recommendations"] = []
    result = _evaluate(
        _report(status=NO_CANARY_RUNS, lifecycle=ACTIVE, payload=payload)
    )
    assert result.status == INSUFFICIENT_DATA


@pytest.mark.parametrize(
    ("lifecycle", "expected"),
    (
        (ACTIVE, INSUFFICIENT_DATA),
        (STOPPED, NOT_ELIGIBLE),
        (EXPIRED, ELIGIBLE_FOR_FULL_LIVE_REVIEW),
        (EXHAUSTED, ELIGIBLE_FOR_FULL_LIVE_REVIEW),
    ),
)
def test_lifecycle_semantics(lifecycle, expected):
    assert _evaluate(_report(lifecycle=lifecycle)).status == expected


def test_expired_five_run_sample_is_insufficient():
    payload = _payload()
    payload["runs"] = payload["runs"][:5]
    payload["run_summary"]["reserved_run_count"] = 5
    payload["run_summary"]["successful_market_universe_run_count"] = 5
    payload["recommendation_evidence"]["recommendations"] = payload[
        "recommendation_evidence"
    ]["recommendations"][:5]
    result = _evaluate(_report(lifecycle=EXPIRED, payload=payload))
    assert result.status == INSUFFICIENT_DATA


def test_failed_run_is_not_eligible():
    payload = _payload()
    payload["runs"][0]["market_universe_analysis_status"] = "FAILED"
    payload["run_summary"]["successful_market_universe_run_count"] = 5
    payload["run_summary"]["failed_market_universe_run_count"] = 1
    result = _evaluate(_report(payload=payload))
    assert result.status == NOT_ELIGIBLE
    assert _check(result, "sample.failed_runs").status == FAIL


def test_unfinished_active_is_insufficient_but_terminal_is_failure():
    payload = _payload()
    payload["runs"][0]["market_universe_analysis_status"] = "RUNNING"
    payload["run_summary"]["successful_market_universe_run_count"] = 5
    payload["run_summary"]["unfinished_market_universe_run_count"] = 1
    assert (
        _evaluate(_report(lifecycle=ACTIVE, payload=payload)).status
        == INSUFFICIENT_DATA
    )
    assert (
        _evaluate(_report(lifecycle=EXHAUSTED, payload=payload)).status == NOT_ELIGIBLE
    )


def test_short_observation_span_is_insufficient():
    payload = _payload()
    for index, item in enumerate(payload["runs"]):
        item["reserved_at"] = STARTED + timedelta(hours=index)
    result = _evaluate(_report(payload=payload))
    assert result.status == INSUFFICIENT_DATA
    assert _check(result, "sample.observation_span").status == "INSUFFICIENT"


def test_terminal_recommendation_coverage_gap_is_failure_but_active_is_insufficient():
    payload = _payload()
    payload["recommendation_evidence"]["recommendations"] = payload[
        "recommendation_evidence"
    ]["recommendations"][:5]
    assert _evaluate(_report(payload=payload)).status == NOT_ELIGIBLE
    assert (
        _evaluate(_report(lifecycle=ACTIVE, payload=payload)).status
        == INSUFFICIENT_DATA
    )


@pytest.mark.parametrize("remove", ["orders", "execution"])
def test_missing_live_buy_sample_is_insufficient(remove):
    payload = _payload()
    if remove == "orders":
        payload["order_evidence"]["orders"] = []
        payload["approval_evidence"]["approval_requests"] = []
    else:
        payload["order_evidence"]["orders"][0].update(
            status="LIVE_CANCELLED",
            executed_quantity=Decimal("0"),
            executed_funds_krw=Decimal("0"),
        )
    result = _evaluate(_report(payload=payload))
    assert result.status == INSUFFICIENT_DATA


def test_approval_bypass_is_not_eligible():
    payload = _payload()
    payload["approval_evidence"]["approval_requests"][0]["status"] = "PENDING"
    result = _evaluate(_report(payload=payload))
    assert result.status == NOT_ELIGIBLE
    assert _check(result, "approval.no_bypass").status == FAIL


def test_per_order_cap_bypass_uses_stored_binding():
    payload = _payload()
    payload["order_evidence"]["orders"][0]["amount_krw"] = Decimal("10001")
    result = _evaluate(_report(payload=payload))
    assert result.status == NOT_ELIGIBLE
    assert _check(result, "safety.per_order_cap").status == FAIL


def test_daily_cap_bypass_uses_counted_statuses_and_kst_day():
    payload = _payload()
    payload["safety_binding_provenance"]["max_buy_order_amount_krw"] = Decimal("20000")
    payload["order_evidence"]["orders"][0]["amount_krw"] = Decimal("16000")
    payload["order_evidence"]["orders"].append(
        {
            **payload["order_evidence"]["orders"][0],
            "order_log_id": 302,
            "recommendation_id": 102,
            "approval_request_id": 202,
            "amount_krw": Decimal("16000"),
            "executed_quantity": Decimal("0.0002"),
            "executed_funds_krw": Decimal("16000"),
        }
    )
    payload["approval_evidence"]["approval_requests"].append(
        {"approval_request_id": 202, "recommendation_id": 102, "status": "APPROVED"}
    )
    result = _evaluate(_report(payload=payload))
    assert result.status == NOT_ELIGIBLE
    assert _check(result, "safety.per_order_cap").status == "PASS"
    assert _check(result, "safety.daily_cap").status == FAIL


@pytest.mark.parametrize(
    "error_code", ["PER_ORDER_LIMIT", "DAILY_LIMIT", "BUDGET_LOCK_BUSY"]
)
def test_normal_safety_block_without_bypass_is_not_failure(error_code):
    payload = _payload()
    payload["operational_evidence"]["alerts"].append(
        {
            "alert_type": "LIVE_CANARY_BUY_LIMIT_BLOCKED",
            "error_code": error_code,
            "delivery_status": "SENT",
            "resolved_at": None,
        }
    )
    assert _evaluate(_report(payload=payload)).status == ELIGIBLE_FOR_FULL_LIVE_REVIEW


@pytest.mark.parametrize(
    ("section", "field", "check_id"),
    (
        (
            "operational_evidence",
            "invalid_provenance_alert_count",
            "safety.invalid_provenance",
        ),
        ("operational_evidence", "pipeline_failure_alert_count", "pipeline.failures"),
        ("order_evidence", "live_failed_count", "orders.live_failed"),
        ("order_evidence", "live_unknown_count", "orders.live_unknown"),
        ("operational_evidence", "alert_delivery_failed_count", "alerts.delivery"),
        (
            "operational_evidence",
            "legacy_unstructured_canary_alert_count",
            "alerts.structured",
        ),
    ),
)
def test_zero_tolerance_runtime_failures(section, field, check_id):
    payload = _payload()
    payload[section]["summary"][field] = 1
    result = _evaluate(_report(payload=payload))
    assert result.status == NOT_ELIGIBLE
    assert _check(result, check_id).status == FAIL


def test_terminal_pending_is_failure_and_active_pending_is_insufficient():
    payload = _payload()
    payload["order_evidence"]["summary"]["pending_order_count"] = 1
    payload["order_evidence"]["orders"][0]["status"] = "LIVE_WAIT"
    assert _evaluate(_report(payload=payload)).status == NOT_ELIGIBLE
    assert (
        _evaluate(_report(lifecycle=ACTIVE, payload=payload)).status
        == INSUFFICIENT_DATA
    )


def test_resolved_stale_alert_is_allowed_but_unresolved_is_failure():
    payload = _payload()
    stale = {
        "alert_type": "STALE_LIVE_ORDER",
        "error_code": None,
        "delivery_status": "SENT",
        "resolved_at": NOW,
    }
    payload["operational_evidence"]["alerts"].append(stale)
    assert _evaluate(_report(payload=payload)).status == ELIGIBLE_FOR_FULL_LIVE_REVIEW
    payload["operational_evidence"]["alerts"][-1]["resolved_at"] = None
    result = _evaluate(_report(payload=payload))
    assert result.status == NOT_ELIGIBLE
    assert _check(result, "orders.unresolved_stale").status == FAIL


def test_ledger_mismatch_is_invalid():
    payload = _payload()
    payload["integrity"]["order_fill_integrity_verified"] = False
    payload["integrity"]["findings"] = ["ORDER_FILL_AGGREGATE_MISMATCH:301"]
    result = _evaluate(_report(payload=payload))
    assert result.status == INVALID_CANARY_DATA
    assert _check(result, "ledger.integrity").status == "INVALID"


def test_fail_has_priority_over_insufficient():
    payload = _payload()
    payload["runs"] = payload["runs"][:5]
    payload["run_summary"]["reserved_run_count"] = 5
    payload["run_summary"]["successful_market_universe_run_count"] = 5
    payload["recommendation_evidence"]["recommendations"] = payload[
        "recommendation_evidence"
    ]["recommendations"][:5]
    payload["order_evidence"]["summary"]["live_unknown_count"] = 1
    assert _evaluate(_report(payload=payload)).status == NOT_ELIGIBLE


def test_invalid_has_priority_over_failure():
    payload = _payload()
    payload["integrity"]["all_canary_runs_verified"] = False
    payload["order_evidence"]["summary"]["live_unknown_count"] = 1
    assert _evaluate(_report(payload=payload)).status == INVALID_CANARY_DATA


def test_policy_compatibility_requires_exact_48_hours_and_six_runs():
    payload = _payload()
    payload["activation_provenance"]["expires_at"] += timedelta(seconds=1)
    assert _evaluate(_report(payload=payload)).status == INVALID_CANARY_DATA


def test_evidence_safety_flag_change_is_invalid():
    payload = _payload()
    payload["safety_flags"]["external_calls"] = True
    assert _evaluate(_report(payload=payload)).status == INVALID_CANARY_DATA


def test_evidence_activation_identity_mismatch_is_invalid_without_decision():
    payload = _payload()
    payload["activation_provenance"]["candidate_id"] = 999
    result = _evaluate(_report(payload=payload))
    assert result.status == INVALID_CANARY_DATA
    assert result.policy_decision_performed is False
    assert result.sample_sufficiency_assessed is False
    assert result.review_decision_signature is None


def test_policy_definition_and_signature_are_deterministic_and_threshold_sensitive():
    assert (
        live_canary_review_policy_definition() == live_canary_review_policy_definition()
    )
    assert (
        live_canary_review_policy_signature() == live_canary_review_policy_signature()
    )
    changed = replace(LIVE_CANARY_REVIEW_GATE_V1, min_observation_span_hours=37)
    assert (
        live_canary_review_policy_signature(changed)
        != live_canary_review_policy_signature()
    )


@pytest.mark.parametrize(
    "policy",
    (
        replace(LIVE_CANARY_REVIEW_GATE_V1, schema_version="other"),
        replace(LIVE_CANARY_REVIEW_GATE_V1, required_successful_run_count=7),
        replace(LIVE_CANARY_REVIEW_GATE_V1, max_failed_run_count=-1),
        replace(
            LIVE_CANARY_REVIEW_GATE_V1,
            min_successful_run_recommendation_coverage=Decimal("1.1"),
        ),
    ),
)
def test_invalid_policy_is_rejected(policy):
    with pytest.raises(LiveCanaryReviewGateError):
        validate_live_canary_review_policy(policy)


def test_decision_signature_covers_evidence_policy_checks_status_and_time():
    result = _evaluate(_report())
    signature = live_canary_review_decision_signature(result)
    assert live_canary_review_decision_signature(result) == signature
    variants = (
        replace(result, evidence_signature="live-canary-evidence-v1:changed"),
        replace(result, review_policy_signature="live-canary-review-gate-v1:changed"),
        replace(result, all_checks=result.all_checks[:-1]),
        replace(result, status=NOT_ELIGIBLE),
        replace(result, evaluated_at=NOW + timedelta(seconds=1)),
    )
    assert all(
        live_canary_review_decision_signature(item) != signature for item in variants
    )


def test_cli_accepts_only_required_activation_id_and_reports_safety_flags():
    assert parse_arguments(["--canary-activation-id", "7"]).canary_activation_id == 7
    for forbidden in ("--apply", "--force", "--threshold", "--promote", "--as-of"):
        with pytest.raises(SystemExit):
            parse_arguments(["--canary-activation-id", "7", forbidden])
    lines = report(_evaluate(_report()))
    assert "status=ELIGIBLE_FOR_FULL_LIVE_REVIEW" in lines
    assert "database_write=false" in lines
    assert "external_calls=false" in lines
    assert "full_live_promotion_performed=false" in lines
