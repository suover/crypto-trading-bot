from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    build_cost_assumptions,
)
from crypto_trading_bot.services.historical_candidate_screening_gate_service import (
    INVALID_SCREENING_DATA,
    NO_CANDIDATES_TO_SCREEN,
    SUCCESS,
    HistoricalCandidateScreeningGateService,
)
from crypto_trading_bot.services.historical_candidate_screening_policy import (
    FAIL,
    INSUFFICIENT,
    INVALID,
    PASS,
    HistoricalCandidateScreeningEvaluator,
    HistoricalScreeningEvidence,
    thresholds_from_promotion_policy,
)
from crypto_trading_bot.services.policy_promotion_gate_service import (
    POLICY_PROMOTION_GATE_V1,
    gate_policy_signature,
)
from crypto_trading_bot.services.reference_bounded_historical_research_batch_service import (
    BATCH_SCHEMA_VERSION,
    NO_NOVEL_CANDIDATES,
    RESEARCH_PROFILE_SCHEMA_VERSION,
    HistoricalResearchCandidateResult,
    HistoricalResearchHorizonEvidence,
    HistoricalResearchProfile,
    ReferenceBoundedHistoricalResearchBatchResult,
)


NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _stats(mean=Decimal("0.1"), positive_rate=Decimal("0.5")):
    return SimpleNamespace(
        fold_statistics=SimpleNamespace(
            statistics=SimpleNamespace(mean_delta=mean, positive_rate=positive_rate)
        )
    )


def _horizon(horizon, **changes):
    values = {
        "horizon_minutes": horizon,
        "baseline_policy_signature": "baseline",
        "effective_top_n": 7,
        "gross_fold_count": 5,
        "cost_fold_count": 5,
        "cost_adjustable_coverage_rate": Decimal("0.70"),
        "gross_status": "SUCCESS",
        "gross_safe_reason": None,
        "gross": SimpleNamespace(),
        "gross_walk_forward_status": "SUCCESS",
        "gross_walk_forward_safe_reason": None,
        "gross_walk_forward": SimpleNamespace(),
        "gross_robustness_status": "SUCCESS",
        "gross_robustness_safe_reason": None,
        "gross_robustness": SimpleNamespace(),
        "cost_adjusted_status": "SUCCESS",
        "cost_adjusted_safe_reason": None,
        "cost_adjusted": SimpleNamespace(),
        "cost_walk_forward_status": "SUCCESS",
        "cost_walk_forward_safe_reason": None,
        "cost_walk_forward": SimpleNamespace(),
        "cost_robustness_status": "SUCCESS",
        "cost_robustness_safe_reason": None,
        "cost_robustness": _stats(),
    }
    values.update(changes)
    return HistoricalResearchHorizonEvidence(**values)


def _candidate(index=1, *, horizons=None):
    return HistoricalResearchCandidateResult(
        scenario_name=f"candidate-{index}",
        scenario_definition_signature=f"signature-{index}",
        component_weights={"liquidity": Decimal("1")},
        donor_field="liquidity",
        receiver_field="trend",
        transfer_step=Decimal("0.05"),
        reference_snapshot_id=39,
        reference_policy_signature="baseline",
        already_registered=False,
        turnover_status="SUCCESS",
        turnover_safe_reason=None,
        turnover_transition_count=5,
        turnover_continuity_break_count=0,
        turnover=SimpleNamespace(),
        horizons=horizons
        or tuple(
            _horizon(value) for value in POLICY_PROMOTION_GATE_V1.required_horizons
        ),
    )


def _batch(candidates=(_candidate(),), *, status="SUCCESS"):
    generated = tuple(
        SimpleNamespace(
            scenario_name=item.scenario_name,
            scenario_definition_signature=item.scenario_definition_signature,
            component_weights=item.component_weights,
            reference_snapshot_id=item.reference_snapshot_id,
            reference_policy_signature=item.reference_policy_signature,
            already_registered=False,
        )
        for item in candidates
    )
    policy = POLICY_PROMOTION_GATE_V1
    return ReferenceBoundedHistoricalResearchBatchResult(
        report_type="REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH",
        batch_schema_version=BATCH_SCHEMA_VERSION,
        research_profile=HistoricalResearchProfile(
            RESEARCH_PROFILE_SCHEMA_VERSION,
            policy.required_horizons,
            policy.historical_initial_research_size,
            policy.historical_validation_size,
            build_cost_assumptions(
                fee_rate=policy.fee_rate,
                spread_cost_rate=policy.spread_cost_rate,
                slippage_rate=policy.slippage_rate,
            ),
        ),
        reference_snapshot_id=39,
        reference_snapshot_captured_at=NOW,
        historical_evidence_as_of=NOW,
        user_id=1,
        exchange="UPBIT",
        quote_asset="KRW",
        dataset_schema_version="strategy-replay-dataset-v1",
        reference_policy_signature="baseline",
        effective_top_n=7,
        generator_schema_version="deterministic-ranking-candidate-generator-v1",
        generator_step=Decimal("0.05"),
        generated_candidate_count=len(candidates),
        already_registered_candidate_count=0,
        novel_candidate_count=len(candidates),
        evaluated_candidate_count=len(candidates),
        historical_timeline_snapshot_count=5,
        historical_timeline_snapshot_ids=(1, 2, 3, 4, 5),
        historical_context_snapshot_count=5,
        historical_context_snapshot_ids=(1, 2, 3, 4, 5),
        generator=SimpleNamespace(all_candidates=generated),
        gross_matrix=None,
        gross_sweep=None,
        gross_walk_forward=None,
        gross_robustness=None,
        turnover=None,
        cost_adjusted=None,
        cost_walk_forward=None,
        cost_robustness=None,
        candidate_results=tuple(candidates),
        status=status,
        safe_reason=None,
    )


def _evaluate(candidate):
    return (
        HistoricalCandidateScreeningGateService(MagicMock())
        .evaluate_from_batch(_batch((candidate,)))
        .candidate_results[0]
    )


def _find(result, check_id, horizon=None):
    return next(
        item
        for item in result.all_checks
        if item.check_id == check_id and item.horizon_minutes == horizon
    )


def test_screening_policy_is_exact_historical_view_of_promotion_policy():
    policy = POLICY_PROMOTION_GATE_V1
    thresholds = thresholds_from_promotion_policy(policy)
    assert thresholds.required_horizons == policy.required_horizons
    assert thresholds.min_gross_fold_count == policy.min_historical_gross_fold_count
    assert thresholds.min_cost_fold_count == policy.min_historical_cost_fold_count
    assert (
        thresholds.min_cost_adjustable_coverage
        == policy.min_historical_cost_adjustable_coverage
    )
    assert (
        thresholds.min_supportive_horizons == policy.min_historical_supportive_horizons
    )
    assert (
        thresholds.supportive_positive_rate
        == policy.historical_supportive_positive_rate
    )
    assert (
        thresholds.catastrophic_mean_delta_floor
        == policy.historical_catastrophic_mean_delta_floor
    )
    assert gate_policy_signature(policy) == gate_policy_signature(policy)


def test_candidate_passes_exact_historical_boundaries_without_side_effects():
    service = HistoricalCandidateScreeningGateService(MagicMock())
    result = service.evaluate_from_batch(_batch())
    candidate = result.candidate_results[0]
    assert result.status == SUCCESS
    assert candidate.status == PASS
    assert result.pass_count == 1
    assert not result.automatic_policy_selection
    assert not result.candidate_registration_performed
    assert not result.database_write
    assert not result.external_calls
    assert result.sample_sufficiency_assessed
    assert not result.statistical_inference_performed


@pytest.mark.parametrize(
    "changes,check_id",
    [
        ({"gross_fold_count": 4}, "HISTORICAL_GROSS_FOLD_COUNT"),
        ({"cost_fold_count": 4}, "HISTORICAL_COST_FOLD_COUNT"),
        (
            {"cost_adjustable_coverage_rate": Decimal("0.69")},
            "HISTORICAL_COST_COVERAGE",
        ),
    ],
)
def test_fold_and_coverage_shortfalls_are_insufficient(changes, check_id):
    horizons = (_horizon(60, **changes), _horizon(240), _horizon(1440))
    result = _evaluate(_candidate(horizons=horizons))
    assert result.status == INSUFFICIENT
    assert _find(result, check_id, 60).status == INSUFFICIENT


def test_supportive_horizon_failure_is_fail():
    horizons = tuple(
        _horizon(value, cost_robustness=_stats(mean=Decimal("0")))
        for value in (60, 240, 1440)
    )
    result = _evaluate(_candidate(horizons=horizons))
    assert result.status == FAIL
    assert _find(result, "HISTORICAL_SUPPORTIVE_HORIZONS").status == FAIL


def test_catastrophic_degradation_is_fail():
    horizons = (
        _horizon(60, cost_robustness=_stats(mean=Decimal("-0.51"))),
        _horizon(240),
        _horizon(1440),
    )
    result = _evaluate(_candidate(horizons=horizons))
    assert result.status == FAIL
    assert _find(result, "HISTORICAL_CATASTROPHIC_DEGRADATION").status == FAIL


def test_insufficient_has_priority_over_fail():
    horizons = (
        _horizon(60, gross_fold_count=4),
        _horizon(240, cost_robustness=_stats(mean=Decimal("0"))),
        _horizon(1440, cost_robustness=_stats(mean=Decimal("0"))),
    )
    assert _evaluate(_candidate(horizons=horizons)).status == INSUFFICIENT


@pytest.mark.parametrize(
    "horizons",
    [
        (_horizon(60), _horizon(240)),
        (_horizon(60), _horizon(60), _horizon(1440)),
        (_horizon(60), _horizon(240), _horizon(999)),
    ],
)
def test_missing_duplicate_or_unexpected_horizon_is_invalid(horizons):
    assert _evaluate(_candidate(horizons=horizons)).status == INVALID


@pytest.mark.parametrize(
    "change",
    [
        {"baseline_policy_signature": "wrong"},
        {"effective_top_n": 8},
        {"cost_adjustable_coverage_rate": Decimal("NaN")},
        {"cost_robustness_status": "INVALID_COST_ADJUSTED_ROBUSTNESS_DATA"},
    ],
)
def test_identity_non_finite_and_upstream_invalid_have_invalid_priority(change):
    horizons = (_horizon(60, **change), _horizon(240), _horizon(1440))
    assert _evaluate(_candidate(horizons=horizons)).status == INVALID


def test_upstream_insufficient_remains_insufficient():
    horizons = (
        _horizon(60, cost_walk_forward_status="NO_COST_ADJUSTABLE_SNAPSHOTS"),
        _horizon(240),
        _horizon(1440),
    )
    assert _evaluate(_candidate(horizons=horizons)).status == INSUFFICIENT


def test_missing_stats_with_sufficient_fold_count_is_invalid():
    horizons = (_horizon(60, cost_robustness=None), _horizon(240), _horizon(1440))
    result = _evaluate(_candidate(horizons=horizons))
    assert result.status == INVALID
    assert _find(result, "HISTORICAL_STABILITY_STATISTICS", 60).status == INVALID


def test_no_candidates_is_normal_and_invalid_batch_fails_closed():
    no_candidates = _batch((), status=NO_NOVEL_CANDIDATES)
    result = HistoricalCandidateScreeningGateService(MagicMock()).evaluate_from_batch(
        no_candidates
    )
    assert result.status == NO_CANDIDATES_TO_SCREEN
    assert result.candidate_count == 0
    invalid = replace(no_candidates, batch_schema_version="wrong")
    result = HistoricalCandidateScreeningGateService(MagicMock()).evaluate_from_batch(
        invalid
    )
    assert result.status == INVALID_SCREENING_DATA


def test_all_41_candidates_are_screened_in_batch_order_without_ranking():
    candidates = tuple(_candidate(index) for index in range(1, 42))
    result = HistoricalCandidateScreeningGateService(MagicMock()).evaluate_from_batch(
        _batch(candidates)
    )
    assert result.candidate_count == result.pass_count == 41
    assert tuple(item.scenario_name for item in result.candidate_results) == tuple(
        item.scenario_name for item in candidates
    )


def test_evaluate_from_batch_does_not_call_batch_or_write_session():
    session = MagicMock()
    batch_service = MagicMock()
    service = HistoricalCandidateScreeningGateService(
        session, batch_service=batch_service
    )
    service.evaluate_from_batch(_batch())
    batch_service.evaluate.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    session.delete.assert_not_called()


def test_shared_evaluator_rejects_out_of_range_positive_rate():
    evaluator = HistoricalCandidateScreeningEvaluator(
        thresholds_from_promotion_policy(POLICY_PROMOTION_GATE_V1)
    )
    evidence = tuple(
        HistoricalScreeningEvidence(
            horizon, 5, 5, Decimal("0.70"), Decimal("0.1"), Decimal("1.1")
        )
        for horizon in (60, 240, 1440)
    )
    result = evaluator.evaluate(evidence)
    assert any(item.status == INVALID for item in result.all_checks)
