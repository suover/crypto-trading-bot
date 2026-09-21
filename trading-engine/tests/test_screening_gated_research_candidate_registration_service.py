from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.historical_candidate_screening_gate_service import (
    SUCCESS as SCREENING_SUCCESS,
)
from crypto_trading_bot.services.historical_candidate_screening_policy import (
    FAIL,
    INSUFFICIENT,
    INVALID,
    PASS,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ALREADY_REGISTERED,
    CREATED as REGISTRY_CREATED,
    DRY_RUN,
    ResearchPolicyCandidateRegistrationAnchor,
)
from crypto_trading_bot.services.screening_gated_research_candidate_registration_service import (
    CREATED,
    INVALID_SCREENING_RESULT,
    NO_PASS_CANDIDATES,
    READY,
    REGISTRATION_FAILED,
    STALE_REFERENCE,
    STALE_REGISTRATION_PLAN,
    STALE_REGISTRATION_STATE,
    ScreeningGatedResearchCandidateRegistrationService,
    registration_plan_signature,
)


NOW = datetime(2026, 9, 21, tzinfo=UTC)
WEIGHTS = {
    "liquidity": Decimal("0.30"),
    "trend_alignment": Decimal("0.25"),
    "momentum": Decimal("0.15"),
    "volume_confirmation": Decimal("0.10"),
    "spread": Decimal("0.08"),
    "volatility": Decimal("0.07"),
    "drawdown": Decimal("0.05"),
}


def _definition(name):
    return parse_scenario_document(
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [{"name": name, "component_weights": WEIGHTS}],
        }
    )[0]


def _candidate(name="candidate-a", status=PASS):
    definition = _definition(name)
    return SimpleNamespace(
        scenario_name=name,
        scenario_definition_signature=definition.definition_signature,
        component_weights=definition.component_weights,
        donor_field="liquidity",
        receiver_field="trend_alignment",
        transfer_step=Decimal("0.05"),
        status=status,
    )


def _screening(candidates):
    values = tuple(candidates)
    return SimpleNamespace(
        status=SCREENING_SUCCESS,
        safe_reason=None,
        invalid_count=sum(item.status == INVALID for item in values),
        pass_count=sum(item.status == PASS for item in values),
        fail_count=sum(item.status == FAIL for item in values),
        insufficient_count=sum(item.status == INSUFFICIENT for item in values),
        candidate_count=len(values),
        candidate_results=values,
        historical_evidence_as_of=NOW - timedelta(minutes=1),
        gate_schema_version="historical-candidate-screening-gate-v1",
        screening_policy_schema_version="historical-candidate-screening-policy-v1",
        screening_policy_signature="screening-policy:one",
        source_promotion_gate_policy_schema_version="policy-promotion-gate-v1",
        source_promotion_gate_policy_signature="promotion-policy:one",
    )


def _anchor(snapshot_id=52, **changes):
    values = {
        "reference_snapshot_id": snapshot_id,
        "reference_snapshot_captured_at": NOW,
        "user_id": 7,
        "exchange": "UPBIT",
        "quote_asset": "KRW",
        "dataset_schema_version": "strategy-replay-dataset-v1",
        "baseline_policy_signature": "baseline",
        "effective_top_n": 7,
        "registration_snapshot_id_watermark": snapshot_id,
        "registration_captured_at_watermark": NOW,
    }
    values.update(changes)
    return ResearchPolicyCandidateRegistrationAnchor(**values)


def _preview(candidate, anchor, status=DRY_RUN):
    return SimpleNamespace(
        registration_status=status,
        registration_created=False,
        candidate=None,
        plan=SimpleNamespace(
            reference_snapshot_id=anchor.reference_snapshot_id,
            reference_snapshot_captured_at=anchor.reference_snapshot_captured_at,
            baseline_policy_signature=anchor.baseline_policy_signature,
            effective_top_n=anchor.effective_top_n,
            registration_snapshot_id_watermark=(
                anchor.registration_snapshot_id_watermark
            ),
            registration_captured_at_watermark=(
                anchor.registration_captured_at_watermark
            ),
            scenario_name=candidate.scenario_name,
            scenario_definition_signature=candidate.scenario_definition_signature,
            component_weights={
                key: str(value) for key, value in candidate.component_weights.items()
            },
        ),
    )


def _service(candidates, *, anchors=None):
    session = MagicMock()
    screening_service = MagicMock()
    screening_service.evaluate.return_value = _screening(candidates)
    registry = MagicMock()
    anchor_values = list(anchors or [_anchor()] * 10)
    freshness = MagicMock(side_effect=anchor_values)
    for candidate in candidates:
        if candidate.status == PASS:
            registry.preview.side_effect = None
            break
    pass_candidates = [item for item in candidates if item.status == PASS]
    registry.preview.side_effect = [
        _preview(item, anchor_values[0]) for item in pass_candidates
    ]
    service = ScreeningGatedResearchCandidateRegistrationService(
        session,
        screening_service=screening_service,
        registry_service=registry,
        freshness_fn=freshness,
        snapshot_lock_fn=MagicMock(),
    )
    return service, session, screening_service, registry, freshness


def test_preview_registers_all_pass_candidates_without_ranking_or_writes():
    candidates = (
        _candidate("candidate-b", PASS),
        _candidate("candidate-fail", FAIL),
        _candidate("candidate-a", PASS),
        _candidate("candidate-short", INSUFFICIENT),
    )
    service, session, _, registry, _ = _service(candidates)
    result = service.preview(reference_snapshot_id=52)
    assert result.status == READY
    assert result.pass_candidate_count == result.registration_candidate_count == 2
    assert tuple(
        item.scenario_name for item in result.registration_plan.candidates
    ) == (
        "candidate-b",
        "candidate-a",
    )
    assert not result.automatic_policy_selection
    assert not result.database_write
    assert not result.candidate_registration_performed
    assert registry.preview.call_count == 2
    registry.register_with_shared_anchor.assert_not_called()
    service.snapshot_lock_fn.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_no_pass_and_invalid_screening_block_registration():
    service, _, _, registry, _ = _service((_candidate(status=FAIL),))
    assert service.preview(reference_snapshot_id=52).status == NO_PASS_CANDIDATES
    registry.preview.assert_not_called()
    service, _, _, registry, _ = _service((_candidate(status=INVALID),))
    assert service.preview(reference_snapshot_id=52).status == INVALID_SCREENING_RESULT
    registry.preview.assert_not_called()


def test_stale_reference_before_or_during_screening_is_blocked():
    service, _, screening, _, _ = _service((_candidate(),), anchors=[])
    service.freshness_fn = MagicMock(side_effect=Exception("stale"))
    assert service.preview(reference_snapshot_id=52).status == REGISTRATION_FAILED
    screening.evaluate.assert_not_called()
    old = _anchor()
    newer = replace(
        old,
        reference_snapshot_id=53,
        registration_snapshot_id_watermark=53,
        reference_snapshot_captured_at=NOW + timedelta(minutes=1),
        registration_captured_at_watermark=NOW + timedelta(minutes=1),
    )
    service, _, _, _, _ = _service((_candidate(),), anchors=[old, newer])
    assert service.preview(reference_snapshot_id=52).status == STALE_REFERENCE


def test_plan_signature_is_deterministic_and_sensitive_to_pass_set_and_policy():
    candidates = (_candidate("candidate-a"), _candidate("candidate-b"))
    first, *_ = _service(candidates)
    second, *_ = _service(candidates)
    a = first.preview(reference_snapshot_id=52)
    b = second.preview(reference_snapshot_id=52)
    assert a.registration_plan_signature == b.registration_plan_signature
    assert (
        registration_plan_signature(a.registration_plan)
        == a.registration_plan_signature
    )
    changed, *_ = _service((candidates[0],))
    assert (
        changed.preview(reference_snapshot_id=52).registration_plan_signature
        != a.registration_plan_signature
    )
    policy_changed, *_ = _service(candidates)
    policy_changed.screening_service.evaluate.return_value = (
        replace(
            _screening(candidates), screening_policy_signature="screening-policy:two"
        )
        if hasattr(_screening(candidates), "__dataclass_fields__")
        else _screening(candidates)
    )
    policy_changed.screening_service.evaluate.return_value.screening_policy_signature = "screening-policy:two"
    assert (
        policy_changed.preview(reference_snapshot_id=52).registration_plan_signature
        != a.registration_plan_signature
    )


def test_apply_requires_exact_fresh_plan_signature_before_lock_or_write():
    service, _, _, registry, _ = _service((_candidate(),))
    result = service.apply(reference_snapshot_id=52, expected_plan_signature="wrong")
    assert result.status == STALE_REGISTRATION_PLAN
    service.snapshot_lock_fn.assert_not_called()
    registry.register_with_shared_anchor.assert_not_called()


def test_apply_uses_short_lock_and_shared_anchor_for_all_candidates():
    candidates = (_candidate("candidate-a"), _candidate("candidate-b"))
    preview_service, *_ = _service(candidates)
    preview = preview_service.preview(reference_snapshot_id=52)
    service, session, _, registry, _ = _service(candidates)
    shared = NOW + timedelta(seconds=5)
    registry.trusted_registration_time.return_value = shared
    registry.register_with_shared_anchor.side_effect = [
        SimpleNamespace(
            registration_status=REGISTRY_CREATED,
            registration_created=True,
            candidate=SimpleNamespace(id=index),
            plan=SimpleNamespace(
                registered_at=shared,
                registration_snapshot_id_watermark=52,
                registration_captured_at_watermark=NOW,
            ),
        )
        for index in (101, 102)
    ]
    result = service.apply(
        reference_snapshot_id=52,
        expected_plan_signature=preview.registration_plan_signature,
    )
    assert result.status == CREATED
    assert result.created_candidate_count == 2
    assert result.shared_registered_at == shared
    assert {item.registered_at for item in result.candidate_results} == {shared}
    assert {
        item.registration_snapshot_id_watermark for item in result.candidate_results
    } == {52}
    service.snapshot_lock_fn.assert_called_once()
    assert registry.register_with_shared_anchor.call_count == 2
    session.commit.assert_not_called()


def test_apply_critical_section_orders_lock_recheck_clock_then_writes():
    candidate = _candidate()
    preview_service, *_ = _service((candidate,))
    signature = preview_service.preview(
        reference_snapshot_id=52
    ).registration_plan_signature
    events = []
    anchor = _anchor()
    screening = MagicMock()
    screening.evaluate.return_value = _screening((candidate,))
    registry = MagicMock()
    registry.preview.return_value = _preview(candidate, anchor)
    registry.trusted_registration_time.side_effect = lambda: (
        events.append("clock") or NOW
    )
    registry.register_with_shared_anchor.side_effect = lambda **kwargs: (
        events.append("write")
        or SimpleNamespace(
            registration_status=REGISTRY_CREATED,
            registration_created=True,
            candidate=SimpleNamespace(id=1),
            plan=SimpleNamespace(
                registered_at=NOW,
                registration_snapshot_id_watermark=52,
                registration_captured_at_watermark=NOW,
            ),
        )
    )
    calls = 0

    def fresh(_):
        nonlocal calls
        calls += 1
        if calls == 4:
            events.append("recheck")
        return anchor

    service = ScreeningGatedResearchCandidateRegistrationService(
        MagicMock(),
        screening_service=screening,
        registry_service=registry,
        freshness_fn=fresh,
        snapshot_lock_fn=lambda: events.append("lock"),
    )
    result = service.apply(reference_snapshot_id=52, expected_plan_signature=signature)
    assert result.status == CREATED
    assert events == ["lock", "recheck", "clock", "write"]


@pytest.mark.parametrize("second_status", [ALREADY_REGISTERED, "BROKEN"])
def test_partial_or_concurrent_registration_aborts_outer_savepoint(second_status):
    candidates = (_candidate("candidate-a"), _candidate("candidate-b"))
    preview_service, *_ = _service(candidates)
    signature = preview_service.preview(
        reference_snapshot_id=52
    ).registration_plan_signature
    service, _, _, registry, _ = _service(candidates)
    registry.trusted_registration_time.return_value = NOW
    registry.register_with_shared_anchor.side_effect = [
        SimpleNamespace(
            registration_status=REGISTRY_CREATED,
            registration_created=True,
            candidate=SimpleNamespace(id=1),
            plan=SimpleNamespace(
                registered_at=NOW,
                registration_snapshot_id_watermark=52,
                registration_captured_at_watermark=NOW,
            ),
        ),
        SimpleNamespace(
            registration_status=second_status,
            registration_created=False,
            candidate=None,
            plan=None,
        ),
    ]
    result = service.apply(reference_snapshot_id=52, expected_plan_signature=signature)
    assert result.status == STALE_REGISTRATION_STATE
    assert not result.database_write
    assert result.created_candidate_count == 0


def test_unexpected_second_insert_error_rolls_back_batch_result():
    candidates = (_candidate("candidate-a"), _candidate("candidate-b"))
    preview_service, *_ = _service(candidates)
    signature = preview_service.preview(
        reference_snapshot_id=52
    ).registration_plan_signature
    service, _, _, registry, _ = _service(candidates)
    registry.trusted_registration_time.return_value = NOW
    registry.register_with_shared_anchor.side_effect = RuntimeError("boom")
    result = service.apply(reference_snapshot_id=52, expected_plan_signature=signature)
    assert result.status == REGISTRATION_FAILED
    assert not result.database_write


def test_apply_final_watermark_change_is_stale_plan():
    candidate = _candidate()
    preview_service, *_ = _service((candidate,))
    signature = preview_service.preview(
        reference_snapshot_id=52
    ).registration_plan_signature
    old = _anchor()
    changed = replace(
        old,
        registration_snapshot_id_watermark=53,
        registration_captured_at_watermark=NOW + timedelta(minutes=1),
    )
    service, _, _, registry, _ = _service(
        (candidate,), anchors=[old, old, old, changed]
    )
    result = service.apply(reference_snapshot_id=52, expected_plan_signature=signature)
    assert result.status == STALE_REGISTRATION_PLAN
    registry.register_with_shared_anchor.assert_not_called()


def test_default_snapshot_lock_conflicts_with_snapshot_writers():
    session = MagicMock()
    service = ScreeningGatedResearchCandidateRegistrationService(
        session,
        screening_service=MagicMock(),
        registry_service=MagicMock(),
    )
    service._lock_snapshot_writes()
    statement = str(session.execute.call_args.args[0])
    assert statement == "LOCK TABLE strategy_replay_snapshots IN SHARE MODE"
