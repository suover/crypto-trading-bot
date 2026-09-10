from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
)
from crypto_trading_bot.services.policy_promotion_gate_service import (
    ELIGIBLE_FOR_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_PROMOTION_DATA,
    NOT_ELIGIBLE,
    POLICY_PROMOTION_GATE_V1,
    PASS,
    PolicyPromotionGateResult,
    PromotionGateCheckResult,
    gate_policy_signature,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    parse_scenario_document,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ALREADY_ENROLLED,
    CREATED,
    DRY_RUN,
    GATE_NOT_ELIGIBLE,
    INVALID_SHADOW_ENROLLMENT,
    ShadowPolicyEnrollmentService,
    gate_decision_signature,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    load_and_validate_shadow_policy_enrollment,
)
from scripts.enroll_shadow_policy import parse_arguments, report, run


NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _scenario():
    return parse_scenario_document(
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [
                {
                    "name": "shadow-candidate",
                    "component_weights": {
                        "liquidity": "0.2",
                        "trend_alignment": "0",
                        "momentum": "0.8",
                        "volume_confirmation": "0",
                        "spread": "0",
                        "volatility": "0",
                        "drawdown": "0",
                    },
                }
            ],
        }
    )[0]


def _candidate(**changes):
    scenario = _scenario()
    value = ForwardCandidateMetadata(
        candidate_id=3,
        candidate_schema_version="research-policy-candidate-v1",
        scenario_name="shadow-candidate",
        scenario_definition_signature=scenario.definition_signature,
        component_weights=scenario.component_weights,
        user_id=7,
        exchange="UPBIT",
        quote_asset="KRW",
        baseline_policy_signature="baseline-signature",
        effective_top_n=7,
        dataset_schema_version="strategy-replay-dataset-v1",
        reference_snapshot_id=8,
        reference_snapshot_captured_at=NOW - timedelta(days=10),
        registered_at=NOW - timedelta(days=9),
        registration_snapshot_id_watermark=10,
        registration_captured_at_watermark=NOW - timedelta(days=10),
    )
    return replace(value, **changes)


def _gate(**changes):
    candidate = changes.pop("candidate", _candidate())
    check = PromotionGateCheckResult(
        check_id="ALL_REQUIRED_EVIDENCE",
        category="FORWARD_PERFORMANCE",
        status=PASS,
        horizon_minutes=60,
        observed_value="1",
        comparator=">=",
        threshold_value="0",
        reason=None,
    )
    values = dict(
        candidate_id=candidate.candidate_id,
        candidate=candidate,
        gate_policy_schema_version=POLICY_PROMOTION_GATE_V1.schema_version,
        gate_policy_signature=gate_policy_signature(POLICY_PROMOTION_GATE_V1),
        gate_policy=POLICY_PROMOTION_GATE_V1,
        evaluated_at=NOW,
        forward_snapshot_id_ceiling=40,
        status=ELIGIBLE_FOR_REVIEW,
        safe_reason=None,
        passed_checks=(check,),
        insufficient_checks=(),
        failed_checks=(),
        invalid_checks=(),
        all_checks=(check,),
        historical=SimpleNamespace(historical_candidate_snapshot_ids=(1, 2, 3)),
        forward_gross=SimpleNamespace(eligible_forward_snapshot_ids=(11, 20, 40)),
        forward_turnover=SimpleNamespace(
            forward_timeline_snapshot_ids=(11, 20, 40),
            candidate_context_snapshot_ids=(11, 20, 40),
        ),
        forward_cost_adjusted=SimpleNamespace(
            horizons=(
                SimpleNamespace(
                    horizon_minutes=60,
                    cost_adjustable_forward_snapshot_ids=(20, 40),
                ),
                SimpleNamespace(
                    horizon_minutes=240,
                    cost_adjustable_forward_snapshot_ids=(20, 40),
                ),
                SimpleNamespace(
                    horizon_minutes=1440,
                    cost_adjustable_forward_snapshot_ids=(20, 40),
                ),
            )
        ),
        sample_sufficiency_assessed=True,
        statistical_inference_performed=False,
        policy_decision_performed=True,
        promotion_performed=False,
        shadow_policy_created=False,
        database_write=False,
        external_calls=False,
        live_policy_change=False,
    )
    values.update(changes)
    return PolicyPromotionGateResult(**values)


def _service(monkeypatch, gate=None, existing=None, watermark=None, now=None):
    candidate = _candidate()
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_enrollment_service.load_and_validate_forward_candidate",
        lambda _session, _candidate_id: SimpleNamespace(metadata=candidate),
    )
    session = MagicMock()
    session.scalar.return_value = existing
    session.execute.return_value.one.return_value = watermark or (
        42,
        NOW + timedelta(hours=1),
    )
    gate_service = MagicMock()
    gate_service.evaluate.return_value = gate or _gate()
    service = ShadowPolicyEnrollmentService(
        session,
        gate_service=gate_service,
        now_fn=lambda: now or NOW + timedelta(hours=2),
    )
    return service, session, gate_service


def test_eligible_preview_is_read_only_and_does_not_create_authoritative_anchor(
    monkeypatch,
):
    service, session, gate_service = _service(monkeypatch)
    result = service.preview(candidate_id=3)
    assert result.enrollment_status == DRY_RUN
    assert result.gate_eligible
    assert result.enrollment is None
    assert not result.database_write
    gate_service.evaluate.assert_called_once_with(candidate_id=3)
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("status", [INSUFFICIENT_DATA, NOT_ELIGIBLE])
def test_gate_blocked_apply_never_writes(monkeypatch, status):
    service, session, _ = _service(monkeypatch, gate=_gate(status=status))
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == GATE_NOT_ELIGIBLE
    assert not result.database_write
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_invalid_gate_is_fail_closed(monkeypatch):
    service, session, _ = _service(
        monkeypatch, gate=_gate(status=INVALID_PROMOTION_DATA)
    )
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == INVALID_SHADOW_ENROLLMENT
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_sufficiency_assessed": False},
        {"statistical_inference_performed": True},
        {"policy_decision_performed": False},
        {"promotion_performed": True},
        {"shadow_policy_created": True},
        {"database_write": True},
        {"external_calls": True},
        {"live_policy_change": True},
        {"forward_snapshot_id_ceiling": 10},
        {"invalid_checks": (SimpleNamespace(status="INVALID"),)},
        {"insufficient_checks": (SimpleNamespace(status="INSUFFICIENT"),)},
        {"failed_checks": (SimpleNamespace(status="FAIL"),)},
        {"all_checks": (SimpleNamespace(status="FAIL"),)},
        {"gate_policy_schema_version": "other"},
        {"gate_policy_signature": "wrong"},
        {
            "gate_policy": replace(
                POLICY_PROMOTION_GATE_V1,
                min_forward_successful_gross_snapshots=22,
            )
        },
        {"candidate": _candidate(effective_top_n=8)},
    ],
)
def test_inconsistent_eligible_gate_is_invalid(monkeypatch, changes):
    service, session, _ = _service(monkeypatch, gate=_gate(**changes))
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == INVALID_SHADOW_ENROLLMENT
    session.add.assert_not_called()


def test_new_enrollment_captures_new_broad_watermark_and_service_does_not_commit(
    monkeypatch,
):
    service, session, gate_service = _service(monkeypatch)
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == CREATED
    assert result.database_write
    assert result.enrollment.shadow_snapshot_id_watermark == 42
    assert result.enrollment.gate_forward_snapshot_id_ceiling == 40
    assert result.enrollment.shadow_enrolled_at == NOW + timedelta(hours=2)
    assert result.enrollment.gate_policy_definition["required_horizons"] == [
        60,
        240,
        1440,
    ]
    assert result.enrollment.gate_checks[0]["status"] == PASS
    gate_service.evaluate.assert_called_once_with(candidate_id=3)
    session.add.assert_called_once_with(result.enrollment)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()


def test_existing_valid_enrollment_is_returned_before_gate(monkeypatch):
    first_service, _, _ = _service(monkeypatch)
    created = first_service.enroll(candidate_id=3).enrollment
    service, session, gate_service = _service(monkeypatch, existing=created)
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == ALREADY_ENROLLED
    assert result.enrollment is created
    assert not result.gate_evaluated
    assert not result.database_write
    gate_service.evaluate.assert_not_called()
    session.add.assert_not_called()


def test_shared_shadow_provenance_loader_validates_enrollment(monkeypatch):
    first_service, _, _ = _service(monkeypatch)
    enrollment = first_service.enroll(candidate_id=3).enrollment
    candidate = _candidate()
    session = MagicMock()
    session.scalar.return_value = enrollment
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_provenance.load_and_validate_forward_candidate",
        lambda _session, _candidate_id: SimpleNamespace(metadata=candidate),
    )

    validated = load_and_validate_shadow_policy_enrollment(session, 3)

    assert validated.row is enrollment
    assert validated.candidate == candidate
    assert (
        validated.scenario.definition_signature
        == candidate.scenario_definition_signature
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("scenario_definition_signature", "corrupt"),
        ("component_weights", {"liquidity": "1"}),
        ("baseline_policy_signature", "corrupt"),
        ("effective_top_n", 8),
        ("candidate_registration_snapshot_id_watermark", 9),
        ("gate_policy_signature", "corrupt"),
        ("gate_checks", [{"status": "FAIL"}]),
        ("gate_evidence_provenance", {}),
        ("gate_decision_signature", "corrupt"),
        ("gate_status", "NOT_ELIGIBLE"),
    ],
)
def test_existing_corruption_is_invalid_without_gate_rerun(monkeypatch, field, value):
    first_service, _, _ = _service(monkeypatch)
    enrollment = first_service.enroll(candidate_id=3).enrollment
    setattr(enrollment, field, value)
    service, session, gate_service = _service(monkeypatch, existing=enrollment)
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == INVALID_SHADOW_ENROLLMENT
    gate_service.evaluate.assert_not_called()
    session.add.assert_not_called()


def test_decision_signature_is_deterministic_timezone_normalized_and_sensitive():
    gate = _gate()
    assert gate_decision_signature(gate) == gate_decision_signature(gate)
    offset_time = gate.evaluated_at.astimezone(timezone(timedelta(hours=9)))
    assert gate_decision_signature(
        replace(gate, evaluated_at=offset_time)
    ) == gate_decision_signature(gate)
    changed = replace(
        gate,
        all_checks=(replace(gate.all_checks[0], observed_value="2"),),
        passed_checks=(replace(gate.passed_checks[0], observed_value="2"),),
    )
    assert gate_decision_signature(changed) != gate_decision_signature(gate)
    for check_change in (
        {"status": "FAIL"},
        {"horizon_minutes": 240},
        {"threshold_value": "2"},
    ):
        changed_check = replace(gate.all_checks[0], **check_change)
        assert gate_decision_signature(
            replace(gate, all_checks=(changed_check,))
        ) != gate_decision_signature(gate)
    reordered_candidate = replace(
        gate.candidate,
        component_weights=dict(
            reversed(tuple(gate.candidate.component_weights.items()))
        ),
    )
    assert gate_decision_signature(
        replace(gate, candidate=reordered_candidate)
    ) == gate_decision_signature(gate)


def test_watermark_hook_runs_after_trusted_clock_and_before_query(monkeypatch):
    events = []
    service, session, _ = _service(monkeypatch)
    service.now_fn = lambda: events.append("clock") or NOW + timedelta(hours=2)
    service.before_watermark_fn = lambda: events.append("race-insert")
    session.execute.side_effect = lambda _query: (
        events.append("watermark")
        or SimpleNamespace(one=lambda: (43, NOW + timedelta(hours=1)))
    )
    result = service.enroll(candidate_id=3)
    assert result.enrollment_status == CREATED
    assert result.enrollment.shadow_snapshot_id_watermark == 43
    assert events == ["clock", "race-insert", "watermark"]


def test_concurrent_insert_loser_reloads_immutable_winner(monkeypatch):
    first_service, _, _ = _service(monkeypatch)
    winner = first_service.enroll(candidate_id=3).enrollment
    service, session, gate_service = _service(monkeypatch)
    session.scalar.side_effect = [None, winner]
    session.flush.side_effect = IntegrityError("insert", {}, Exception("unique"))

    result = service.enroll(candidate_id=3)

    assert result.enrollment_status == ALREADY_ENROLLED
    assert result.enrollment is winner
    assert result.gate_decision_signature == winner.gate_decision_signature
    gate_service.evaluate.assert_called_once_with(candidate_id=3)
    session.commit.assert_not_called()


def test_cli_allows_only_candidate_and_apply_and_commits_only_created(monkeypatch):
    assert vars(parse_arguments(["--candidate-id", "3"])) == {
        "candidate_id": 3,
        "apply": False,
    }
    with pytest.raises(SystemExit):
        parse_arguments(["--candidate-id", "3", "--force"])
    service, _, _ = _service(monkeypatch)
    result = service.enroll(candidate_id=3)
    output = "\n".join(report(result))
    assert "shadow_runtime_enabled=false" in output
    assert "enrollment_status=CREATED" in output
    fake_session = MagicMock()
    monkeypatch.setattr(
        "scripts.enroll_shadow_policy.ShadowPolicyEnrollmentService.enroll",
        lambda _self, candidate_id: result,
    )
    _, exit_code = run(
        fake_session, parse_arguments(["--candidate-id", "3", "--apply"])
    )
    assert exit_code == 0
    fake_session.commit.assert_called_once_with()


def test_cli_preview_rolls_back_and_never_commits(monkeypatch):
    service, _, _ = _service(monkeypatch)
    result = service.preview(candidate_id=3)
    fake_session = MagicMock()
    monkeypatch.setattr(
        "scripts.enroll_shadow_policy.ShadowPolicyEnrollmentService.preview",
        lambda _self, candidate_id: result,
    )
    _, exit_code = run(fake_session, parse_arguments(["--candidate-id", "3"]))
    assert exit_code == 0
    fake_session.rollback.assert_called_once_with()
    fake_session.commit.assert_not_called()
