from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_performance_service import (
    NO_SHADOW_EVALUATIONS,
    RESULT_TYPE as PERFORMANCE_RESULT_TYPE,
    SHADOW_OUTCOMES_PENDING,
    SUCCESS,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    FAIL,
    INSUFFICIENT,
    INSUFFICIENT_DATA,
    INVALID,
    INVALID_REVIEW_DATA,
    NOT_ELIGIBLE,
    NO_SHADOW_ENROLLMENT,
    PASS,
    SHADOW_REVIEW_GATE_V1,
    ShadowReviewGateService,
    shadow_review_decision_signature,
    shadow_review_policy_signature,
    validate_shadow_review_policy,
)


NOW = datetime(2026, 9, 13, tzinfo=UTC)


def enrollment():
    return SimpleNamespace(
        id=7,
        candidate_id=9,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="shadow-policy",
        scenario_definition_signature="scenario-definition",
        baseline_policy_signature="baseline-policy",
        effective_top_n=7,
        shadow_enrolled_at=NOW - timedelta(days=20),
        gate_policy_signature="pre-shadow-policy",
        gate_decision_signature="pre-shadow-decision",
    )


def gross(horizon, *, count=35, mean=Decimal("0.10"), status=SUCCESS):
    return SimpleNamespace(
        horizon_minutes=horizon,
        eligible_shadow_snapshot_count=42,
        successful_comparable_snapshot_count=count,
        outcome_incomplete_count=42 - count,
        invalid_outcome_count=0,
        shadow_win_count=count,
        shadow_loss_count=0,
        tie_count=0,
        shadow_win_rate=Decimal("1"),
        mean_return_delta=mean,
        status=status,
    )


def cost(
    horizon,
    *,
    eligible=42,
    count=35,
    mean=Decimal("0.10"),
    median=Decimal("0.05"),
    win_rate=Decimal("0.60"),
    status=SUCCESS,
):
    ids = tuple(range(1, count + 1))
    wins = int(Decimal(count) * win_rate) if count else 0
    actual_win_rate = Decimal(wins) / Decimal(count) if count else None
    return SimpleNamespace(
        horizon_minutes=horizon,
        eligible_shadow_snapshot_count=eligible,
        successful_gross_snapshot_count=max(count, 35),
        shadow_transition_count=max(eligible - 1, 0),
        cost_adjustable_shadow_snapshot_count=count,
        cost_adjustable_shadow_snapshot_ids=ids,
        cost_adjustable_coverage_rate=(
            Decimal(count) / Decimal(eligible) if eligible else None
        ),
        outcome_incomplete_count=eligible - max(count, 35),
        mean_cost_adjusted_return_delta=mean,
        median_cost_adjusted_return_delta=median,
        cost_adjusted_shadow_win_count=wins,
        cost_adjusted_shadow_loss_count=count - wins,
        tie_count=0,
        cost_adjusted_shadow_win_rate=actual_win_rate,
        status=status,
    )


def performance(*, selection_count=42):
    row = enrollment()
    ids = tuple(range(1, selection_count + 1))
    start = NOW - timedelta(hours=336)
    summary = SimpleNamespace(
        transition_count=max(selection_count - 1, 0),
        mean_replacement_rate=Decimal("0.2"),
    )
    turnover = SimpleNamespace(
        timeline_snapshot_ids=ids,
        candidate_context_snapshot_ids=ids,
        successful_selection_snapshot_ids=ids,
        transition_count=max(selection_count - 1, 0),
        continuity_break_count=0,
        baseline_summary=summary,
        shadow_summary=summary,
        mean_replacement_rate_delta_vs_baseline=Decimal("0"),
        status=SUCCESS,
        transitions=tuple(range(max(selection_count - 1, 0))),
    )
    return SimpleNamespace(
        result_type=PERFORMANCE_RESULT_TYPE,
        candidate_id=9,
        enrollment=row,
        performance_evidence_as_of=NOW,
        shadow_evaluation_snapshot_id_ceiling=max(ids),
        requested_horizons=(60, 240, 1440),
        cost_assumptions=SimpleNamespace(
            fee_rate=Decimal("0.0005"),
            spread_cost_rate=Decimal("0.0005"),
            slippage_rate=Decimal("0.001"),
        ),
        timeline_snapshot_ids=ids,
        candidate_context_snapshot_ids=ids,
        successful_selection_snapshot_ids=ids,
        timeline_evaluation_count=selection_count,
        successful_selection_count=selection_count,
        context_mismatch_count=0,
        baseline_integrity_failed_count=0,
        replay_incompatible_count=0,
        first_success_captured_at=start,
        last_success_captured_at=NOW,
        observation_span_hours=Decimal("336"),
        gross=tuple(gross(value) for value in (60, 240, 1440)),
        turnover=turnover,
        cost_adjusted=tuple(cost(value) for value in (60, 240, 1440)),
        status=SUCCESS,
        safe_reason=None,
        shadow_enrollment_verified=True,
        shadow_boundary_enforced=True,
        performance_as_of_enforced=True,
        evaluation_ceiling_enforced=True,
        stored_shadow_selection_reused=True,
        offline_replay_performed=False,
        outcome_data_used=True,
        performance_evaluated=True,
        cost_model_reused=True,
        turnover_continuity_enforced=True,
        database_write=False,
        external_calls=False,
        live_policy_change=False,
        sample_sufficiency_assessed=False,
        statistical_inference_performed=False,
        policy_decision_performed=False,
        promotion_performed=False,
        shadow_runtime_changed=False,
    )


def evaluate(monkeypatch, value):
    row = enrollment()
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_review_gate_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: SimpleNamespace(row=row),
    )
    service = MagicMock()
    service.evaluate.return_value = value
    result = ShadowReviewGateService(
        MagicMock(), performance_service=service, now_fn=lambda: NOW
    ).evaluate(candidate_id=9)
    return result, service


def check(result, check_id):
    return next(item for item in result.all_checks if item.check_id == check_id)


def test_policy_is_exact_immutable_and_signature_is_canonical() -> None:
    policy = SHADOW_REVIEW_GATE_V1
    assert policy.required_horizons == (60, 240, 1440)
    assert policy.min_shadow_observation_span_hours == 336
    assert policy.min_shadow_successful_selection_count == 42
    assert policy.min_shadow_successful_gross_snapshots == 35
    assert policy.min_shadow_turnover_transitions == 40
    assert policy.min_shadow_cost_adjustable_snapshots == 35
    assert policy.min_shadow_cost_adjustable_coverage == Decimal("0.80")
    assert policy.min_supportive_gross_horizons == 2
    assert policy.min_supportive_cost_adjusted_horizons == 2
    assert policy.max_shadow_continuity_break_count == 0
    assert shadow_review_policy_signature(policy) == shadow_review_policy_signature(
        replace(policy, fee_rate=Decimal("0.000500"))
    )
    assert shadow_review_policy_signature(policy) != shadow_review_policy_signature(
        replace(policy, fee_rate=Decimal("0.0006"))
    )


@pytest.mark.parametrize(
    "policy",
    (
        replace(SHADOW_REVIEW_GATE_V1, schema_version="future"),
        replace(SHADOW_REVIEW_GATE_V1, required_horizons=(60, 60, 1440)),
        replace(SHADOW_REVIEW_GATE_V1, required_horizons=(240, 60, 1440)),
        replace(SHADOW_REVIEW_GATE_V1, min_shadow_successful_selection_count=0),
        replace(SHADOW_REVIEW_GATE_V1, fee_rate=Decimal("Infinity")),
        replace(SHADOW_REVIEW_GATE_V1, fee_rate=Decimal("1")),
        replace(SHADOW_REVIEW_GATE_V1, min_supportive_gross_horizons=4),
        replace(SHADOW_REVIEW_GATE_V1, max_shadow_continuity_break_count=-1),
    ),
)
def test_invalid_policy_is_rejected(policy) -> None:
    with pytest.raises(ReplayInputError):
        validate_shadow_review_policy(policy)


def test_no_enrollment_skips_performance(monkeypatch) -> None:
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_review_gate_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: None,
    )
    performance_service = MagicMock()
    result = ShadowReviewGateService(
        MagicMock(), performance_service=performance_service, now_fn=lambda: NOW
    ).evaluate(candidate_id=9)
    assert result.status == NO_SHADOW_ENROLLMENT
    assert result.sample_sufficiency_assessed is False
    assert result.policy_decision_performed is False
    performance_service.evaluate.assert_not_called()


def test_valid_evidence_calls_performance_once_with_only_frozen_policy(
    monkeypatch,
) -> None:
    result, service = evaluate(monkeypatch, performance())
    assert result.status == ELIGIBLE_FOR_PROMOTION_REVIEW
    service.evaluate.assert_called_once_with(
        candidate_id=9,
        horizons=(60, 240, 1440),
        fee_rate=Decimal("0.0005"),
        spread_cost_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
    )
    assert result.sample_sufficiency_assessed is True
    assert result.policy_decision_performed is True
    assert result.statistical_inference_performed is False
    assert result.promotion_performed is False
    assert result.database_write is False
    assert result.external_calls is False
    assert result.live_policy_change is False
    assert result.shadow_runtime_changed is False
    assert len({item.check_id for item in result.all_checks}) == len(result.all_checks)


def test_no_shadow_evaluations_is_insufficient(monkeypatch) -> None:
    value = performance()
    value.status = NO_SHADOW_EVALUATIONS
    value.shadow_evaluation_snapshot_id_ceiling = None
    value.timeline_snapshot_ids = ()
    value.candidate_context_snapshot_ids = ()
    value.successful_selection_snapshot_ids = ()
    value.gross = ()
    value.turnover = None
    value.cost_adjusted = ()
    result, _ = evaluate(monkeypatch, value)
    assert result.status == INSUFFICIENT_DATA
    assert result.insufficient_checks


def test_pending_1440_outcome_is_insufficient_not_failed(monkeypatch) -> None:
    value = performance()
    pending_gross = gross(1440, count=0, mean=None, status=SHADOW_OUTCOMES_PENDING)
    pending_gross.shadow_win_rate = None
    pending_cost = cost(1440, count=0, status=SHADOW_OUTCOMES_PENDING)
    pending_cost.successful_gross_snapshot_count = 0
    pending_cost.outcome_incomplete_count = 42
    pending_cost.mean_cost_adjusted_return_delta = None
    pending_cost.median_cost_adjusted_return_delta = None
    pending_cost.cost_adjusted_shadow_win_rate = None
    value.gross = (gross(60), gross(240), pending_gross)
    value.cost_adjusted = (cost(60), cost(240), pending_cost)
    result, _ = evaluate(monkeypatch, value)
    assert result.status == INSUFFICIENT_DATA
    assert check(result, "shadow.gross.1440.mean_delta").status == INSUFFICIENT
    assert check(result, "shadow.gross.60.mean_delta").status == PASS
    assert not result.failed_checks


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: setattr(value, "timeline_snapshot_ids", (1, 1)),
        lambda value: setattr(value, "shadow_evaluation_snapshot_id_ceiling", 1),
        lambda value: setattr(
            value.cost_adjusted[0],
            "cost_adjustable_shadow_snapshot_ids",
            (999,) + value.cost_adjusted[0].cost_adjustable_shadow_snapshot_ids[1:],
        ),
    ),
)
def test_snapshot_list_corruption_is_invalid(monkeypatch, mutate) -> None:
    value = performance()
    mutate(value)
    result, _ = evaluate(monkeypatch, value)
    assert result.status == INVALID_REVIEW_DATA


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("result_type", "WRONG"),
        ("candidate_id", 10),
        ("requested_horizons", (60, 240)),
        ("stored_shadow_selection_reused", False),
        ("offline_replay_performed", True),
        ("database_write", True),
        ("external_calls", True),
        ("live_policy_change", True),
        ("performance_as_of_enforced", False),
        ("evaluation_ceiling_enforced", False),
        ("cost_model_reused", False),
        ("turnover_continuity_enforced", False),
    ),
)
def test_performance_provenance_mismatch_is_invalid(
    monkeypatch, field, bad_value
) -> None:
    value = performance()
    setattr(value, field, bad_value)
    result, _ = evaluate(monkeypatch, value)
    assert result.status == INVALID_REVIEW_DATA
    assert result.policy_decision_performed is False


@pytest.mark.parametrize(
    ("field", "observed", "expected"),
    (
        ("span", Decimal("335"), INSUFFICIENT),
        ("span", Decimal("336"), PASS),
        ("gross_count", 34, INSUFFICIENT),
        ("gross_count", 35, PASS),
        ("turnover", 39, INSUFFICIENT),
        ("turnover", 40, PASS),
        ("cost_count", 34, INSUFFICIENT),
        ("cost_count", 35, PASS),
    ),
)
def test_sample_boundaries(monkeypatch, field, observed, expected) -> None:
    value = performance()
    if field == "span":
        value.first_success_captured_at = NOW - timedelta(hours=int(observed))
        value.observation_span_hours = observed
        check_id = "shadow.observation_span_hours"
    elif field == "gross_count":
        value.gross = tuple(gross(h, count=observed) for h in (60, 240, 1440))
        value.cost_adjusted = tuple(
            cost(h, count=min(observed, 35)) for h in (60, 240, 1440)
        )
        for item in value.cost_adjusted:
            item.successful_gross_snapshot_count = observed
            item.outcome_incomplete_count = 42 - observed
        check_id = "shadow.gross.60.successful_snapshot_count"
    elif field == "turnover":
        value.turnover.transition_count = observed
        value.turnover.transitions = tuple(range(observed))
        value.turnover.baseline_summary.transition_count = observed
        for item in value.cost_adjusted:
            item.shadow_transition_count = observed
        check_id = "shadow.turnover.transition_count"
    else:
        value.cost_adjusted = tuple(cost(h, count=observed) for h in (60, 240, 1440))
        check_id = "shadow.cost.60.cost_adjustable_count"
    result, _ = evaluate(monkeypatch, value)
    assert check(result, check_id).status == expected


@pytest.mark.parametrize(("count", "expected"), ((41, INSUFFICIENT), (42, PASS)))
def test_selection_count_boundary(monkeypatch, count, expected) -> None:
    value = performance(selection_count=count)
    value.gross = tuple(gross(h) for h in (60, 240, 1440))
    value.cost_adjusted = tuple(cost(h, eligible=count) for h in (60, 240, 1440))
    for item in value.gross:
        item.eligible_shadow_snapshot_count = count
        item.outcome_incomplete_count = (
            count - item.successful_comparable_snapshot_count
        )
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.successful_selection_count").status == expected


@pytest.mark.parametrize(("count", "expected"), ((79, INSUFFICIENT), (80, PASS)))
def test_cost_coverage_boundary(monkeypatch, count, expected) -> None:
    value = performance(selection_count=100)
    value.gross = tuple(gross(h) for h in (60, 240, 1440))
    value.cost_adjusted = tuple(
        cost(h, eligible=100, count=count) for h in (60, 240, 1440)
    )
    for item in value.gross:
        item.eligible_shadow_snapshot_count = 100
        item.successful_comparable_snapshot_count = 100
        item.outcome_incomplete_count = 0
        item.shadow_win_count = 100
        item.shadow_win_rate = Decimal("1")
    for item in value.cost_adjusted:
        item.successful_gross_snapshot_count = 100
        item.outcome_incomplete_count = 0
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.cost.60.coverage").status == expected


@pytest.mark.parametrize(
    ("means", "expected"),
    (
        ((Decimal("0"), Decimal("0"), Decimal("0")), PASS),
        ((Decimal("0"), Decimal("0"), Decimal("-0.01")), PASS),
        ((Decimal("0"), Decimal("-0.01"), Decimal("-0.01")), FAIL),
    ),
)
def test_gross_supportive_horizon_count(monkeypatch, means, expected) -> None:
    value = performance()
    value.gross = tuple(
        gross(horizon, mean=mean)
        for horizon, mean in zip((60, 240, 1440), means, strict=True)
    )
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.gross.supportive_horizon_count").status == expected


@pytest.mark.parametrize(
    ("mean", "expected"), ((Decimal("-0.50"), PASS), (Decimal("-0.5001"), FAIL))
)
def test_gross_catastrophic_floor(monkeypatch, mean, expected) -> None:
    value = performance()
    value.gross = (gross(60, mean=mean), gross(240), gross(1440))
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.gross.60.catastrophic_floor").status == expected


@pytest.mark.parametrize(
    ("mean", "median", "win_rate", "expected"),
    (
        (Decimal("0.01"), Decimal("0"), Decimal("0.55"), PASS),
        (Decimal("0"), Decimal("0"), Decimal("0.55"), FAIL),
        (Decimal("0.01"), Decimal("-0.01"), Decimal("0.55"), FAIL),
        (Decimal("0.01"), Decimal("0"), Decimal("0.549"), FAIL),
    ),
)
def test_cost_supportive_definition(
    monkeypatch, mean, median, win_rate, expected
) -> None:
    value = performance(selection_count=1000)
    value.gross = tuple(gross(h) for h in (60, 240, 1440))
    for item in value.gross:
        item.eligible_shadow_snapshot_count = 1000
        item.successful_comparable_snapshot_count = 1000
        item.outcome_incomplete_count = 0
        item.shadow_win_count = 1000
        item.shadow_win_rate = Decimal("1")
    value.cost_adjusted = (
        cost(
            60,
            eligible=1000,
            count=1000,
            mean=mean,
            median=median,
            win_rate=win_rate,
        ),
        cost(240, eligible=1000, count=1000),
        cost(1440, eligible=1000, count=1000),
    )
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.cost.60.mean_delta").status == (
        PASS if mean > 0 else FAIL
    )
    if expected == FAIL:
        assert result.status == NOT_ELIGIBLE


@pytest.mark.parametrize(
    ("mean", "expected"), ((Decimal("-0.50"), PASS), (Decimal("-0.5001"), FAIL))
)
def test_cost_catastrophic_floor(monkeypatch, mean, expected) -> None:
    value = performance()
    value.cost_adjusted = (cost(60, mean=mean), cost(240), cost(1440))
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.cost.60.catastrophic_floor").status == expected


@pytest.mark.parametrize(("breaks", "expected"), ((0, PASS), (1, FAIL)))
def test_continuity_break_boundary(monkeypatch, breaks, expected) -> None:
    value = performance()
    value.turnover.continuity_break_count = breaks
    result, _ = evaluate(monkeypatch, value)
    assert check(result, "shadow.turnover.continuity_break_count").status == expected


def test_status_priority_is_invalid_then_insufficient_then_fail() -> None:
    service = ShadowReviewGateService(MagicMock(), now_fn=lambda: NOW)
    make = service._check
    common = dict(
        candidate_id=9,
        enrollment=None,
        evaluated_at=NOW,
        performance=None,
        provenance_verified=False,
        performance_verified=False,
        decision_performed=False,
    )
    assert (
        service._result(
            checks=(
                make("fail", "X", FAIL),
                make("low", "X", INSUFFICIENT),
                make("bad", "X", INVALID),
            ),
            **common,
        ).status
        == INVALID_REVIEW_DATA
    )
    assert (
        service._result(
            checks=(make("fail", "X", FAIL), make("low", "X", INSUFFICIENT)),
            **common,
        ).status
        == INSUFFICIENT_DATA
    )
    assert (
        service._result(checks=(make("fail", "X", FAIL),), **common).status
        == NOT_ELIGIBLE
    )


def test_decision_signature_is_deterministic_and_time_canonical(monkeypatch) -> None:
    first, _ = evaluate(monkeypatch, performance())
    second, _ = evaluate(monkeypatch, performance())
    assert first.review_decision_signature == second.review_decision_signature
    equivalent = replace(
        first,
        evaluated_at=first.evaluated_at.astimezone(timezone(timedelta(hours=9))),
        review_decision_signature=None,
    )
    assert shadow_review_decision_signature(first) == shadow_review_decision_signature(
        equivalent
    )
    changed = deepcopy(performance())
    changed.shadow_evaluation_snapshot_id_ceiling += 1
    changed_result, _ = evaluate(monkeypatch, changed)
    assert first.review_decision_signature != changed_result.review_decision_signature
