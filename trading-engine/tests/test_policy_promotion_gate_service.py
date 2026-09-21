from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    build_cost_assumptions,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    InvalidForwardCandidateProvenance,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.policy_promotion_gate_service import (
    ELIGIBLE_FOR_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_PROMOTION_DATA,
    NOT_ELIGIBLE,
    POLICY_PROMOTION_GATE_V1,
    PolicyPromotionGateService,
    gate_policy_signature,
    validate_gate_policy,
)
from scripts.evaluate_policy_promotion_gate import parse_arguments, report, run


NOW = datetime(2026, 1, 10, tzinfo=UTC)


def _candidate(**changes):
    value = ForwardCandidateMetadata(
        candidate_id=1,
        candidate_schema_version="research-policy-candidate-v1",
        scenario_name="candidate",
        scenario_definition_signature="scenario-signature",
        component_weights={"liquidity": Decimal("1")},
        user_id=7,
        exchange="UPBIT",
        quote_asset="KRW",
        baseline_policy_signature="baseline-signature",
        effective_top_n=7,
        dataset_schema_version="strategy-replay-dataset-v1",
        reference_snapshot_id=100,
        reference_snapshot_captured_at=NOW - timedelta(days=9),
        registered_at=NOW - timedelta(days=8),
        registration_snapshot_id_watermark=100,
        registration_captured_at_watermark=NOW - timedelta(days=9),
    )
    return replace(value, **changes)


def _bundle():
    candidate = _candidate()
    assumptions = build_cost_assumptions(
        fee_rate=Decimal("0.0005"),
        spread_cost_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
    )
    historical_gross = []
    historical_cost = []
    gross_horizons = []
    cost_horizons = []
    forward_ids = tuple(range(101, 122))
    for horizon in (60, 240, 1440):
        historical_gross.append(SimpleNamespace(horizon_minutes=horizon, fold_count=5))
        stats = SimpleNamespace(
            mean_delta=Decimal("0.10"), positive_rate=Decimal("0.50")
        )
        historical_cost.append(
            SimpleNamespace(
                horizon_minutes=horizon,
                fold_count=5,
                cost_adjustable_coverage_rate=Decimal("0.70"),
                scenario_results=(
                    SimpleNamespace(fold_statistics=SimpleNamespace(statistics=stats)),
                ),
            )
        )
        snapshots = tuple(
            SimpleNamespace(
                snapshot_id=snapshot_id,
                captured_at=candidate.registered_at
                + timedelta(hours=1 + (index * 8.4)),
            )
            for index, snapshot_id in enumerate(forward_ids)
        )
        gross_horizons.append(
            SimpleNamespace(
                horizon_minutes=horizon,
                successful_comparable_snapshot_count=21,
                mean_return_delta=Decimal("0"),
                snapshots=snapshots,
            )
        )
        cost_horizons.append(
            SimpleNamespace(
                horizon_minutes=horizon,
                cost_adjustable_forward_snapshot_count=20,
                cost_adjustable_coverage_rate=Decimal("0.80"),
                mean_cost_adjusted_return_delta=Decimal("0.01"),
                median_cost_adjusted_return_delta=Decimal("0"),
                cost_adjusted_win_rate=Decimal("0.55"),
            )
        )
    historical = SimpleNamespace(
        candidate_id=1,
        candidate=candidate,
        historical_evidence_as_of=candidate.registered_at,
        historical_candidate_snapshot_ids=tuple(range(90, 101)),
        requested_horizons=(60, 240, 1440),
        assumptions=assumptions,
        gross_robustness=SimpleNamespace(cohorts=tuple(historical_gross)),
        cost_robustness=SimpleNamespace(cohorts=tuple(historical_cost)),
        status="SUCCESS",
        candidate_registration_verified=True,
        registration_time_evidence_enforced=True,
        post_registration_snapshots_excluded=True,
        post_registration_outcomes_excluded=True,
        snapshot_created_at_cutoff_enforced=True,
        outcome_evaluated_at_cutoff_enforced=True,
        outcome_created_at_cutoff_enforced=True,
        outcome_updated_at_cutoff_enforced=True,
        historical_forward_snapshot_disjointness_verified=True,
        historical_strict_unseen_validation="not_verified",
    )
    gross = SimpleNamespace(
        candidate_id=1,
        candidate=candidate,
        requested_horizons=(60, 240, 1440),
        eligible_forward_snapshot_ids=forward_ids,
        status="SUCCESS",
        candidate_registration_verified=True,
        forward_anchor_enforced=True,
        pre_registration_snapshots_excluded=True,
        registration_time_provenance_verified=True,
        future_snapshot_cutoff_verified=True,
        scenario_definition_frozen_at_registration=True,
        forward_validation_performed=True,
        horizons=tuple(gross_horizons),
    )
    turnover = SimpleNamespace(
        candidate_id=1,
        candidate=candidate,
        forward_timeline_snapshot_ids=forward_ids,
        transition_count=20,
        continuity_break_count=0,
        status="SUCCESS",
        candidate_registration_verified=True,
        forward_anchor_enforced=True,
        pre_registration_snapshots_excluded=True,
        pre_registration_transition_excluded=True,
        forward_continuity_enforced=True,
        first_forward_snapshot_has_no_prior_forward_transition=True,
    )
    cost = SimpleNamespace(
        candidate_id=1,
        candidate=candidate,
        requested_horizons=(60, 240, 1440),
        assumptions=assumptions,
        gross_status="SUCCESS",
        turnover_status="SUCCESS",
        gross_forward_snapshot_ids=forward_ids,
        turnover_transition_count=20,
        continuity_break_count=0,
        status="SUCCESS",
        candidate_registration_verified=True,
        forward_anchor_enforced=True,
        pre_registration_snapshots_excluded=True,
        pre_registration_transition_excluded=True,
        forward_continuity_enforced=True,
        cost_model_reused=True,
        horizons=tuple(cost_horizons),
    )
    return historical, gross, turnover, cost


def _evaluate(bundle=None):
    values = bundle or _bundle()
    return PolicyPromotionGateService(MagicMock()).evaluate_from_results(
        *values, forward_snapshot_id_ceiling=121, evaluated_at=NOW
    )


def _find(result, check_id, horizon=None):
    return next(
        item
        for item in result.all_checks
        if item.check_id == check_id and item.horizon_minutes == horizon
    )


def test_v1_policy_and_signature_are_exact_and_deterministic():
    policy = POLICY_PROMOTION_GATE_V1
    assert policy.required_horizons == (60, 240, 1440)
    assert policy.min_forward_successful_gross_snapshots == 21
    assert policy.min_forward_turnover_transitions == 20
    assert policy.min_forward_observation_span_hours == 168
    assert gate_policy_signature(policy) == gate_policy_signature(policy)
    assert gate_policy_signature(
        replace(policy, min_forward_turnover_transitions=21)
    ) != gate_policy_signature(policy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("fee_rate", True),
        ("min_forward_cost_adjustable_coverage", Decimal("NaN")),
        ("min_forward_turnover_transitions", False),
        ("min_forward_turnover_transitions", 0),
    ],
)
def test_policy_rejects_invalid_numeric_thresholds(field, value):
    with pytest.raises(ReplayInputError):
        validate_gate_policy(replace(POLICY_PROMOTION_GATE_V1, **{field: value}))


def test_all_exact_v1_boundaries_are_eligible_and_side_effect_free():
    result = _evaluate()
    assert result.status == ELIGIBLE_FOR_REVIEW
    assert not result.insufficient_checks
    assert not result.failed_checks
    assert not result.invalid_checks
    assert result.sample_sufficiency_assessed
    assert result.policy_decision_performed
    assert not result.statistical_inference_performed
    assert not result.promotion_performed
    assert not result.shadow_policy_created
    assert not result.database_write
    assert not result.external_calls
    assert not result.live_policy_change
    categories = [item.category for item in result.all_checks]
    assert categories == sorted(
        categories,
        key=(
            "INTEGRITY",
            "SUFFICIENCY",
            "HISTORICAL_STABILITY",
            "FORWARD_PERFORMANCE",
        ).index,
    )


@pytest.mark.parametrize(
    "mutation,check_id,horizon",
    [
        (
            lambda b: setattr(b[0].gross_robustness.cohorts[0], "fold_count", 4),
            "HISTORICAL_GROSS_FOLD_COUNT",
            60,
        ),
        (
            lambda b: setattr(b[0].cost_robustness.cohorts[0], "fold_count", 4),
            "HISTORICAL_COST_FOLD_COUNT",
            60,
        ),
        (
            lambda b: setattr(
                b[0].cost_robustness.cohorts[0],
                "cost_adjustable_coverage_rate",
                Decimal("0.69"),
            ),
            "HISTORICAL_COST_COVERAGE",
            60,
        ),
        (
            lambda b: setattr(
                b[1].horizons[0], "successful_comparable_snapshot_count", 20
            ),
            "FORWARD_GROSS_SAMPLE_COUNT",
            60,
        ),
        (
            lambda b: (
                setattr(b[2], "transition_count", 19)
                or setattr(b[3], "turnover_transition_count", 19)
            ),
            "FORWARD_TURNOVER_TRANSITION_COUNT",
            None,
        ),
        (
            lambda b: setattr(
                b[3].horizons[0], "cost_adjustable_forward_snapshot_count", 19
            ),
            "FORWARD_COST_SAMPLE_COUNT",
            60,
        ),
        (
            lambda b: setattr(
                b[3].horizons[0], "cost_adjustable_coverage_rate", Decimal("0.79")
            ),
            "FORWARD_COST_COVERAGE",
            60,
        ),
        (
            lambda b: setattr(
                b[1].horizons[0].snapshots[-1],
                "captured_at",
                b[1].horizons[0].snapshots[0].captured_at
                + timedelta(hours=167, minutes=59),
            ),
            "FORWARD_OBSERVATION_SPAN",
            None,
        ),
    ],
)
def test_sufficiency_below_boundary_is_insufficient(mutation, check_id, horizon):
    bundle = deepcopy(_bundle())
    mutation(bundle)
    result = _evaluate(bundle)
    assert result.status == INSUFFICIENT_DATA
    assert _find(result, check_id, horizon).status == "INSUFFICIENT"


@pytest.mark.parametrize(
    "mutation,check_id,horizon",
    [
        (
            lambda b: (
                setattr(
                    b[0]
                    .cost_robustness.cohorts[0]
                    .scenario_results[0]
                    .fold_statistics.statistics,
                    "mean_delta",
                    Decimal("0"),
                )
                or setattr(
                    b[0]
                    .cost_robustness.cohorts[1]
                    .scenario_results[0]
                    .fold_statistics.statistics,
                    "mean_delta",
                    Decimal("0"),
                )
            ),
            "HISTORICAL_SUPPORTIVE_HORIZONS",
            None,
        ),
        (
            lambda b: setattr(
                b[0]
                .cost_robustness.cohorts[0]
                .scenario_results[0]
                .fold_statistics.statistics,
                "mean_delta",
                Decimal("-0.51"),
            ),
            "HISTORICAL_CATASTROPHIC_DEGRADATION",
            None,
        ),
        (
            lambda b: setattr(b[1].horizons[0], "mean_return_delta", Decimal("-0.01")),
            "FORWARD_GROSS_MEAN_DELTA",
            60,
        ),
        (
            lambda b: setattr(
                b[3].horizons[0], "mean_cost_adjusted_return_delta", Decimal("0")
            ),
            "FORWARD_COST_MEAN_DELTA",
            60,
        ),
        (
            lambda b: setattr(
                b[3].horizons[0], "median_cost_adjusted_return_delta", Decimal("-0.01")
            ),
            "FORWARD_COST_MEDIAN_DELTA",
            60,
        ),
        (
            lambda b: setattr(
                b[3].horizons[0], "cost_adjusted_win_rate", Decimal("0.549")
            ),
            "FORWARD_COST_WIN_RATE",
            60,
        ),
        (
            lambda b: (
                setattr(b[2], "continuity_break_count", 1)
                or setattr(b[3], "continuity_break_count", 1)
            ),
            "FORWARD_CONTINUITY",
            None,
        ),
    ],
)
def test_sufficient_performance_failure_is_not_eligible(mutation, check_id, horizon):
    bundle = deepcopy(_bundle())
    mutation(bundle)
    result = _evaluate(bundle)
    assert result.status == NOT_ELIGIBLE
    assert _find(result, check_id, horizon).status == "FAIL"


def test_insufficient_precedes_failure():
    bundle = deepcopy(_bundle())
    bundle[2].transition_count = 19
    bundle[3].turnover_transition_count = 19
    bundle[1].horizons[0].mean_return_delta = Decimal("-1")
    assert _evaluate(bundle).status == INSUFFICIENT_DATA


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: setattr(b[1], "candidate", replace(b[1].candidate, candidate_id=2)),
        lambda b: setattr(b[0], "historical_evidence_as_of", NOW),
        lambda b: setattr(b[0], "post_registration_snapshots_excluded", False),
        lambda b: setattr(b[0], "historical_strict_unseen_validation", "verified"),
        lambda b: setattr(b[1], "eligible_forward_snapshot_ids", (100,)),
        lambda b: setattr(b[2], "forward_timeline_snapshot_ids", (122,)),
        lambda b: setattr(
            b[3],
            "assumptions",
            build_cost_assumptions(
                fee_rate=Decimal("0.001"),
                spread_cost_rate=Decimal("0.0005"),
                slippage_rate=Decimal("0.001"),
            ),
        ),
        lambda b: setattr(b[1], "requested_horizons", (60, 240)),
    ],
)
def test_integrity_corruption_fails_closed(mutation):
    bundle = deepcopy(_bundle())
    mutation(bundle)
    result = _evaluate(bundle)
    assert result.status == INVALID_PROMOTION_DATA
    assert result.invalid_checks


def test_invalid_precedes_insufficient():
    bundle = deepcopy(_bundle())
    bundle[2].transition_count = 1
    bundle[1].eligible_forward_snapshot_ids = (100,)
    assert _evaluate(bundle).status == INVALID_PROMOTION_DATA


def test_gate_calls_each_upstream_once_and_reuses_forward_results(monkeypatch):
    historical, gross, turnover, cost = _bundle()
    historical_service = MagicMock()
    historical_service.evaluate.return_value = historical
    gross_service = MagicMock()
    gross_service.evaluate.return_value = gross
    turnover_service = MagicMock()
    turnover_service.evaluate.return_value = turnover
    cost_service = MagicMock()
    cost_service.evaluate_from_results.return_value = cost
    session = MagicMock()
    session.scalar.return_value = 121
    monkeypatch.setattr(
        "crypto_trading_bot.services.policy_promotion_gate_service.load_and_validate_forward_candidate",
        lambda _session, _candidate_id: SimpleNamespace(metadata=historical.candidate),
    )
    result = PolicyPromotionGateService(
        session,
        historical_service=historical_service,
        forward_gross_service=gross_service,
        forward_turnover_service=turnover_service,
        forward_cost_service=cost_service,
        now_fn=lambda: NOW,
    ).evaluate(candidate_id=1)
    assert result.status == ELIGIBLE_FOR_REVIEW
    historical_service.evaluate.assert_called_once()
    gross_service.evaluate.assert_called_once_with(
        candidate_id=1, horizons=(60, 240, 1440), snapshot_id_ceiling=121
    )
    turnover_service.evaluate.assert_called_once_with(
        candidate_id=1, snapshot_id_ceiling=121
    )
    cost_service.evaluate.assert_not_called()
    cost_service.evaluate_from_results.assert_called_once()


def test_candidate_provenance_error_returns_invalid_without_upstream_calls(monkeypatch):
    services = [MagicMock() for _ in range(4)]
    monkeypatch.setattr(
        "crypto_trading_bot.services.policy_promotion_gate_service.load_and_validate_forward_candidate",
        MagicMock(side_effect=InvalidForwardCandidateProvenance("corrupt candidate")),
    )
    result = PolicyPromotionGateService(
        MagicMock(),
        historical_service=services[0],
        forward_gross_service=services[1],
        forward_turnover_service=services[2],
        forward_cost_service=services[3],
        now_fn=lambda: NOW,
    ).evaluate(candidate_id=1)
    assert result.status == INVALID_PROMOTION_DATA
    assert result.candidate_id == 1
    assert not services[0].evaluate.called
    assert not services[1].evaluate.called
    assert not services[2].evaluate.called
    assert not services[3].evaluate.called
    assert not services[3].evaluate_from_results.called


@pytest.mark.parametrize("service_name", ["gross", "turnover"])
@pytest.mark.parametrize("ceiling", [None, 100, 121])
def test_forward_ceiling_validation_accepts_backward_compatible_values(
    service_name, ceiling
):
    if service_name == "gross":
        from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
            ForwardCandidateGrossEvidenceService,
        )

        validator = ForwardCandidateGrossEvidenceService._validate_ceiling
    else:
        from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
            ForwardCandidateTurnoverEvidenceService,
        )

        validator = ForwardCandidateTurnoverEvidenceService._validate_ceiling
    validator(ceiling, 100)


@pytest.mark.parametrize("ceiling", [True, 99, Decimal("100")])
def test_forward_ceiling_validation_rejects_invalid_values(ceiling):
    from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
        ForwardCandidateGrossEvidenceService,
    )
    from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
        ForwardCandidateTurnoverEvidenceService,
    )

    with pytest.raises(ReplayInputError):
        ForwardCandidateGrossEvidenceService._validate_ceiling(ceiling, 100)
    with pytest.raises(ReplayInputError):
        ForwardCandidateTurnoverEvidenceService._validate_ceiling(ceiling, 100)


def test_cli_accepts_only_candidate_id_and_reports_auditable_decision(monkeypatch):
    namespace = parse_arguments(["--candidate-id", "1"])
    assert vars(namespace) == {"candidate_id": 1}
    with pytest.raises(SystemExit):
        parse_arguments(["--candidate-id", "1", "--threshold", "0"])
    result = _evaluate()
    output = "\n".join(report(result))
    assert "report_type=POLICY_PROMOTION_GATE" in output
    assert "result_type=POLICY_PROMOTION_GATE_V1_DECISION" in output
    assert "historical_strict_unseen_validation=not_verified" in output
    assert "min_forward_successful_gross_snapshots=21" in output
    assert "status=ELIGIBLE_FOR_REVIEW" in output
    monkeypatch.setattr(
        "scripts.evaluate_policy_promotion_gate.PolicyPromotionGateService.evaluate",
        lambda _self, candidate_id: result,
    )
    lines, exit_code = run(MagicMock(), namespace)
    assert exit_code == 0
    assert lines == report(result)
