from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    CostAdjustedRankingEvaluationService,
    build_cost_assumptions,
    compute_selection_change_cost,
)
from crypto_trading_bot.services.forward_candidate_cost_adjusted_evidence_service import (
    FORWARD_OUTCOMES_PENDING,
    INSUFFICIENT_FORWARD_TRANSITIONS,
    INVALID_FORWARD_COST_ADJUSTED_EVIDENCE,
    NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS,
    NO_FORWARD_SNAPSHOTS,
    SUCCESS,
    ForwardCandidateCostAdjustedEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    NO_COMPARABLE_FORWARD_SNAPSHOTS,
    NO_FORWARD_SNAPSHOTS as GROSS_NO_FORWARD,
    FORWARD_OUTCOMES_PENDING as GROSS_PENDING,
    SUCCESS as GROSS_SUCCESS,
    ForwardCandidateGrossEvidenceResult,
    ForwardCandidateHorizonEvidence,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
)
from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
    INSUFFICIENT_FORWARD_TRANSITIONS as TURNOVER_INSUFFICIENT,
    SUCCESS as TURNOVER_SUCCESS,
    NO_FORWARD_SNAPSHOTS as TURNOVER_NO_FORWARD,
    ForwardCandidateTurnoverEvidenceResult,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    OUTCOME_INCOMPLETE,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    RankingSelectionTransition,
    TemporalRankingTurnoverScenarioTransition,
    TemporalRankingTurnoverTransition,
)
from scripts import evaluate_forward_candidate_cost_adjusted_evidence as cli


NOW = datetime(2060, 1, 2, tzinfo=UTC)
WEIGHTS = {
    "liquidity": Decimal("0.20"),
    "trend_alignment": Decimal("0.20"),
    "momentum": Decimal("0.30"),
    "volume_confirmation": Decimal("0.10"),
    "spread": Decimal("0.08"),
    "volatility": Decimal("0.07"),
    "drawdown": Decimal("0.05"),
}


def _metadata(**changes):
    values = {
        "candidate_id": 5,
        "candidate_schema_version": "research-policy-candidate-v1",
        "scenario_name": "candidate-a",
        "scenario_definition_signature": "ranking-scenario-definition-v1:abc",
        "component_weights": WEIGHTS,
        "user_id": 3,
        "exchange": "UPBIT",
        "quote_asset": "KRW",
        "baseline_policy_signature": "strategy-replay-v1:abc",
        "effective_top_n": 7,
        "dataset_schema_version": "strategy-replay-v1",
        "reference_snapshot_id": 10,
        "reference_snapshot_captured_at": NOW - timedelta(days=2),
        "registered_at": NOW,
        "registration_snapshot_id_watermark": 12,
        "registration_captured_at_watermark": NOW - timedelta(days=1),
    }
    values.update(changes)
    return ForwardCandidateMetadata(**values)


def _markets(prefix: str, replaced: int = 0):
    original = tuple(f"KRW-{prefix}{index}" for index in range(7))
    current = (
        *original[: 7 - replaced],
        *(f"KRW-{prefix}N{index}" for index in range(replaced)),
    )
    return original, current


def _selection_transition(previous_id, current_id, *, prefix, replaced):
    previous, current = _markets(prefix, replaced)
    previous_set = set(previous)
    current_set = set(current)
    retained = tuple(value for value in previous if value in current_set)
    entered = tuple(value for value in current if value not in previous_set)
    exited = tuple(value for value in previous if value not in current_set)
    return RankingSelectionTransition(
        previous_snapshot_id=previous_id,
        current_snapshot_id=current_id,
        previous_captured_at=NOW + timedelta(hours=previous_id),
        current_captured_at=NOW + timedelta(hours=current_id),
        effective_top_n=7,
        previous_top_markets=previous,
        current_top_markets=current,
        retained_markets=retained,
        entered_markets=entered,
        exited_markets=exited,
        retained_count=len(retained),
        entered_count=len(entered),
        exited_count=len(exited),
        retention_rate=Decimal(7 - replaced) / Decimal(7),
        replacement_rate=Decimal(replaced) / Decimal(7),
    )


def _transition(
    previous_id=13, current_id=14, *, baseline_replaced=2, candidate_replaced=1
):
    baseline = _selection_transition(
        previous_id, current_id, prefix="B", replaced=baseline_replaced
    )
    candidate = _selection_transition(
        previous_id, current_id, prefix="C", replaced=candidate_replaced
    )
    scenario = TemporalRankingTurnoverScenarioTransition(
        scenario_name="candidate-a",
        scenario_signature="offline-replay-v1:scenario",
        transition=candidate,
        replacement_rate_delta_vs_baseline=(
            candidate.replacement_rate - baseline.replacement_rate
        ),
    )
    return TemporalRankingTurnoverTransition(
        transition_index=1, baseline=baseline, scenarios=(scenario,)
    )


def _gross_snapshot(
    snapshot_id,
    transition=None,
    *,
    horizon=60,
    status="SUCCESS",
    baseline_return="2",
    candidate_return="3",
):
    success = status == "SUCCESS"
    if transition is None:
        baseline_markets = tuple(f"KRW-B{i}" for i in range(7))
        candidate_markets = tuple(f"KRW-C{i}" for i in range(7))
        captured_at = NOW + timedelta(hours=snapshot_id)
    else:
        baseline_markets = transition.baseline.current_top_markets
        candidate_markets = transition.scenarios[0].transition.current_top_markets
        captured_at = transition.baseline.current_captured_at
    baseline_value = Decimal(baseline_return) if success else None
    candidate_value = Decimal(candidate_return) if success else None
    return StrategyABSnapshotPerformanceResult(
        snapshot_id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        captured_at=captured_at,
        horizon_minutes=horizon,
        baseline_policy_signature="strategy-replay-v1:abc",
        scenario_signature="offline-replay-v1:scenario" if success else None,
        replay_status="SUCCESS",
        replay_safe_reason=None,
        status=status,
        safe_reason=None if success else "pending",
        performance_evaluated=success,
        effective_top_n=7 if success else 0,
        baseline_top_markets=baseline_markets if success else (),
        scenario_top_markets=candidate_markets if success else (),
        top_n_overlap_count=0,
        top_n_overlap_rate=Decimal("0"),
        entered_top_n=(),
        exited_top_n=(),
        baseline_required_count=7,
        scenario_required_count=7,
        baseline_complete_count=7 if success else 0,
        scenario_complete_count=7 if success else 0,
        baseline_missing_markets=() if success else baseline_markets,
        scenario_missing_markets=() if success else candidate_markets,
        baseline_candidate_count=7,
        scenario_candidate_count=7,
        baseline_mean_return=baseline_value,
        scenario_mean_return=candidate_value,
        mean_return_delta=(candidate_value - baseline_value if success else None),
        baseline_median_return=baseline_value,
        scenario_median_return=candidate_value,
        median_return_delta=(candidate_value - baseline_value if success else None),
        baseline_positive_count=7 if success else None,
        scenario_positive_count=7 if success else None,
        baseline_negative_count=0 if success else None,
        scenario_negative_count=0 if success else None,
        baseline_flat_count=0 if success else None,
        scenario_flat_count=0 if success else None,
        baseline_positive_rate=Decimal("1") if success else None,
        scenario_positive_rate=Decimal("1") if success else None,
        positive_rate_delta=Decimal("0") if success else None,
        scenario_result="SCENARIO_WIN" if success else None,
    )


def _horizon(rows, *, horizon=60, status=GROSS_SUCCESS):
    successes = sum(row.status == "SUCCESS" for row in rows)
    pending = sum(row.status == OUTCOME_INCOMPLETE for row in rows)
    return ForwardCandidateHorizonEvidence(
        horizon_minutes=horizon,
        eligible_forward_snapshot_count=len(rows),
        successful_comparable_snapshot_count=successes,
        outcome_incomplete_count=pending,
        baseline_integrity_failed_count=0,
        replay_incompatible_count=0,
        invalid_outcome_count=0,
        scenario_win_count=successes,
        scenario_loss_count=0,
        tie_count=0,
        scenario_win_rate=Decimal("1") if successes else None,
        mean_baseline_return=Decimal("2") if successes else None,
        mean_scenario_return=Decimal("3") if successes else None,
        mean_return_delta=Decimal("1") if successes else None,
        median_snapshot_return_delta=Decimal("1") if successes else None,
        mean_baseline_positive_rate=Decimal("1") if successes else None,
        mean_scenario_positive_rate=Decimal("1") if successes else None,
        status=status,
        safe_reason=None if status == GROSS_SUCCESS else "pending",
        snapshots=tuple(rows),
    )


def _gross(metadata=None, horizons=None, ids=(13, 14)):
    metadata = metadata or _metadata()
    transition = _transition()
    horizons = horizons or (
        _horizon(
            (
                _gross_snapshot(13),
                _gross_snapshot(14, transition),
            )
        ),
    )
    return ForwardCandidateGrossEvidenceResult(
        candidate_id=metadata.candidate_id,
        candidate=metadata,
        requested_horizons=tuple(item.horizon_minutes for item in horizons),
        eligible_forward_snapshot_ids=ids,
        status=(
            GROSS_NO_FORWARD
            if not ids
            else GROSS_SUCCESS
            if any(item.status == GROSS_SUCCESS for item in horizons)
            else GROSS_PENDING
        ),
        safe_reason=None,
        candidate_registration_verified=True,
        forward_anchor_enforced=True,
        pre_registration_snapshots_excluded=True,
        registration_time_provenance_verified=True,
        future_snapshot_cutoff_verified=True,
        scenario_definition_frozen_at_registration=True,
        forward_evidence_generated=bool(ids),
        forward_validation_performed=True,
        horizons=tuple(horizons),
    )


def _turnover(metadata=None, transitions=None, ids=(13, 14)):
    metadata = metadata or _metadata()
    transitions = (_transition(),) if transitions is None else tuple(transitions)
    return ForwardCandidateTurnoverEvidenceResult(
        candidate_id=metadata.candidate_id,
        candidate=metadata,
        forward_timeline_snapshot_ids=ids,
        candidate_context_snapshot_ids=ids,
        common_replayable_snapshot_ids=ids,
        transition_count=len(transitions),
        continuity_break_count=0,
        baseline_summary=None,
        candidate_summary=None,
        transitions=transitions,
        status=(
            TURNOVER_NO_FORWARD
            if not ids
            else TURNOVER_SUCCESS
            if transitions
            else TURNOVER_INSUFFICIENT
        ),
        safe_reason=None,
        candidate_registration_verified=True,
        forward_anchor_enforced=True,
        pre_registration_snapshots_excluded=True,
        pre_registration_transition_excluded=True,
        forward_continuity_enforced=True,
        first_forward_snapshot_has_no_prior_forward_transition=True,
        outcome_data_used=False,
        cost_data_used=False,
        policy_decision_performed=False,
    )


def _evaluate(gross=None, turnover=None, **rates):
    return ForwardCandidateCostAdjustedEvidenceService(
        MagicMock(), gross_service=MagicMock(), turnover_service=MagicMock()
    ).evaluate_from_results(
        gross or _gross(),
        turnover or _turnover(),
        fee_rate=rates.get("fee_rate", "0.0005"),
        spread_cost_rate=rates.get("spread_cost_rate", "0.0005"),
        slippage_rate=rates.get("slippage_rate", "0.001"),
    )


def test_evaluate_calls_each_upstream_exactly_once_and_from_results_calls_neither():
    gross_service = MagicMock()
    turnover_service = MagicMock()
    gross_service.evaluate.return_value = _gross()
    turnover_service.evaluate.return_value = _turnover()
    service = ForwardCandidateCostAdjustedEvidenceService(
        MagicMock(), gross_service=gross_service, turnover_service=turnover_service
    )
    result = service.evaluate(
        candidate_id=5,
        horizons=(60,),
        fee_rate="0.0005",
        spread_cost_rate="0.0005",
        slippage_rate="0.001",
    )
    assert result.status == SUCCESS
    gross_service.evaluate.assert_called_once_with(candidate_id=5, horizons=(60,))
    turnover_service.evaluate.assert_called_once_with(candidate_id=5)

    service.evaluate_from_results(
        _gross(),
        _turnover(),
        fee_rate="0",
        spread_cost_rate="0",
        slippage_rate="0",
    )
    assert (
        gross_service.evaluate.call_count == turnover_service.evaluate.call_count == 1
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", 6),
        ("scenario_definition_signature", "different"),
        ("component_weights", {**WEIGHTS, "liquidity": Decimal("0.21")}),
        ("baseline_policy_signature", "different"),
        ("effective_top_n", 5),
        ("registered_at", NOW + timedelta(seconds=1)),
        ("registration_snapshot_id_watermark", 11),
        ("registration_captured_at_watermark", NOW),
    ],
)
def test_candidate_metadata_mismatch_fails_closed(field, value):
    result = _evaluate(turnover=_turnover(metadata=_metadata(**{field: value})))
    assert result.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "candidate metadata" in result.safe_reason


def test_forward_set_mismatch_and_duplicate_gross_snapshot_fail_closed():
    mismatch = _evaluate(turnover=_turnover(ids=(13, 15)))
    assert mismatch.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "snapshot sets" in mismatch.safe_reason

    gross = _gross()
    horizon = replace(
        gross.horizons[0],
        snapshots=(gross.horizons[0].snapshots[0],) * 2,
    )
    duplicate = _evaluate(gross=replace(gross, horizons=(horizon,)))
    assert duplicate.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "Gross snapshot ID" in duplicate.safe_reason


def test_incomplete_upstream_provenance_flag_fails_closed():
    result = _evaluate(gross=replace(_gross(), future_snapshot_cutoff_verified=False))
    assert result.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "provenance flags" in result.safe_reason


def test_first_forward_snapshot_is_not_synthetic_cost_row():
    gross = _gross(
        ids=(13,),
        horizons=(_horizon((_gross_snapshot(13),)),),
    )
    turnover = _turnover(ids=(13,), transitions=())
    result = _evaluate(gross=gross, turnover=turnover)
    assert result.status == INSUFFICIENT_FORWARD_TRANSITIONS
    assert result.turnover_current_snapshot_ids == ()
    assert result.horizons[0].cost_adjustable_forward_snapshot_count == 0
    assert result.horizons[0].snapshots == ()


def test_no_forward_snapshots_is_a_safe_state():
    horizon = _horizon((), status=GROSS_NO_FORWARD)
    result = _evaluate(
        gross=_gross(ids=(), horizons=(horizon,)),
        turnover=_turnover(ids=(), transitions=()),
    )
    assert result.status == NO_FORWARD_SNAPSHOTS
    assert result.horizons[0].status == NO_FORWARD_SNAPSHOTS
    assert result.horizons[0].cost_adjustable_coverage_rate is None


def test_non_pending_failure_with_transition_has_no_cost_adjustable_snapshot():
    transition = _transition()
    rows = (
        _gross_snapshot(13, status=BASELINE_INTEGRITY_FAILED),
        _gross_snapshot(14, transition, status=BASELINE_INTEGRITY_FAILED),
    )
    horizon = _horizon(rows, status=NO_COMPARABLE_FORWARD_SNAPSHOTS)
    horizon = replace(horizon, baseline_integrity_failed_count=2)
    gross = replace(
        _gross(horizons=(horizon,)),
        status=NO_COMPARABLE_FORWARD_SNAPSHOTS,
    )
    result = _evaluate(gross=gross)
    assert result.status == NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS
    assert result.horizons[0].status == NO_FORWARD_COST_ADJUSTABLE_SNAPSHOTS
    assert result.horizons[0].snapshots == ()


def test_only_transition_current_snapshot_is_cost_adjustable():
    result = _evaluate()
    assert result.status == SUCCESS
    horizon = result.horizons[0]
    assert horizon.cost_adjustable_forward_snapshot_ids == (14,)
    assert horizon.cost_adjustable_coverage_rate == Decimal("0.5")
    assert [item.snapshot_id for item in horizon.snapshots] == [14]
    assert all(item.snapshot_id != 13 for item in horizon.snapshots)


def test_forged_pre_registration_transition_fails_closed():
    forged = _transition(previous_id=12, current_id=13)
    turnover = _turnover(transitions=(forged,))
    result = _evaluate(turnover=turnover)
    assert result.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "transition lineage" in result.safe_reason


def test_duplicate_turnover_current_snapshot_fails_closed():
    first = _transition()
    duplicate = replace(_transition(), transition_index=2)
    result = _evaluate(turnover=_turnover(transitions=(first, duplicate)))
    assert result.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "duplicate turnover current" in result.safe_reason


@pytest.mark.parametrize(
    "change", ["captured_at", "baseline", "scenario", "signature", "topn"]
)
def test_cross_result_transition_mismatch_fails_closed(change):
    gross = _gross()
    first, current = gross.horizons[0].snapshots
    if change == "captured_at":
        current = replace(
            current, captured_at=current.captured_at + timedelta(seconds=1)
        )
    elif change == "baseline":
        current = replace(
            current, baseline_top_markets=tuple(reversed(current.baseline_top_markets))
        )
    elif change == "scenario":
        current = replace(
            current, scenario_top_markets=tuple(reversed(current.scenario_top_markets))
        )
    elif change == "signature":
        current = replace(current, scenario_signature="different")
    else:
        current = replace(current, effective_top_n=5)
    result = _evaluate(gross=replace(gross, horizons=(_horizon((first, current)),)))
    assert result.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    assert "lineage" in result.safe_reason


def test_pending_horizon_isolated_from_success_horizon():
    transition = _transition()
    sixty = _horizon((_gross_snapshot(13), _gross_snapshot(14, transition)), horizon=60)
    pending_rows = (
        _gross_snapshot(13, horizon=1440, status=OUTCOME_INCOMPLETE),
        _gross_snapshot(
            14,
            transition,
            horizon=1440,
            status=OUTCOME_INCOMPLETE,
        ),
    )
    daily = _horizon(pending_rows, horizon=1440, status=GROSS_PENDING)
    result = _evaluate(gross=_gross(horizons=(sixty, daily)))
    assert result.status == SUCCESS
    assert result.horizons[0].status == SUCCESS
    assert result.horizons[1].status == FORWARD_OUTCOMES_PENDING
    assert result.horizons[1].cost_adjustable_forward_snapshot_count == 0
    assert result.horizons[1].mean_cost_adjusted_return_delta is None


@pytest.mark.parametrize("replaced", range(8))
def test_canonical_helper_matches_historical_for_top_seven(replaced):
    transition = _selection_transition(13, 14, prefix="X", replaced=replaced)
    assumptions = build_cost_assumptions(
        fee_rate="0.0005", spread_cost_rate="0.0005", slippage_rate="0.001"
    )
    canonical = compute_selection_change_cost(transition, assumptions)
    historical = CostAdjustedRankingEvaluationService._selection_cost(
        transition, assumptions
    )
    assert canonical == historical
    assert canonical.replacement_rate == Decimal(replaced) / Decimal(7)
    assert canonical.sell_notional_ratio == canonical.replacement_rate
    assert canonical.buy_notional_ratio == canonical.replacement_rate
    assert canonical.gross_traded_notional_ratio == 2 * canonical.replacement_rate


def test_current_like_momentum_and_liquidity_cost_math():
    momentum_transition = _transition(baseline_replaced=5, candidate_replaced=5)
    momentum = (
        _evaluate(
            turnover=_turnover(transitions=(momentum_transition,)),
            gross=_gross(
                horizons=(
                    _horizon(
                        (
                            _gross_snapshot(13),
                            _gross_snapshot(14, momentum_transition),
                        )
                    ),
                )
            ),
        )
        .horizons[0]
        .snapshots[0]
    )
    assert (
        momentum.baseline_execution_cost_percentage
        == momentum.candidate_execution_cost_percentage
    )

    liquidity_transition = _transition(baseline_replaced=5, candidate_replaced=4)
    liquidity = (
        _evaluate(
            turnover=_turnover(transitions=(liquidity_transition,)),
            gross=_gross(
                horizons=(
                    _horizon(
                        (
                            _gross_snapshot(13),
                            _gross_snapshot(
                                14,
                                liquidity_transition,
                            ),
                        )
                    ),
                )
            ),
        )
        .horizons[0]
        .snapshots[0]
    )
    assert (
        liquidity.candidate_execution_cost_percentage
        < liquidity.baseline_execution_cost_percentage
    )


def test_adjusted_returns_use_canonical_intermediate_values_and_tie():
    transition = _transition(baseline_replaced=2, candidate_replaced=1)
    gross = _gross(
        horizons=(
            _horizon(
                (
                    _gross_snapshot(13),
                    _gross_snapshot(
                        14,
                        transition,
                        baseline_return="2",
                        candidate_return="2",
                    ),
                )
            ),
        )
    )
    row = (
        _evaluate(gross=gross, turnover=_turnover(transitions=(transition,)))
        .horizons[0]
        .snapshots[0]
    )
    assert row.baseline_cost_adjusted_return == (
        row.baseline_gross_return - row.baseline_execution_cost_percentage
    )
    assert row.candidate_cost_adjusted_return == (
        row.candidate_gross_return - row.candidate_execution_cost_percentage
    )
    assert row.cost_adjusted_return_delta == (
        row.candidate_cost_adjusted_return - row.baseline_cost_adjusted_return
    )
    assert row.cost_adjusted_scenario_result == "COST_ADJUSTED_SCENARIO_WIN"

    zero_cost = (
        _evaluate(
            gross=gross,
            turnover=_turnover(transitions=(transition,)),
            fee_rate="0",
            spread_cost_rate="0",
            slippage_rate="0",
        )
        .horizons[0]
        .snapshots[0]
    )
    assert zero_cost.cost_adjusted_return_delta == 0
    assert zero_cost.cost_adjusted_scenario_result == "COST_ADJUSTED_TIE"


@pytest.mark.parametrize(
    ("baseline_replaced", "candidate_replaced", "candidate_return", "expected"),
    [
        (0, 7, "2.1", "COST_ADJUSTED_SCENARIO_LOSS"),
        (7, 0, "1.9", "COST_ADJUSTED_SCENARIO_WIN"),
    ],
)
def test_cost_can_reverse_or_improve_gross_delta(
    baseline_replaced, candidate_replaced, candidate_return, expected
):
    transition = _transition(
        baseline_replaced=baseline_replaced,
        candidate_replaced=candidate_replaced,
    )
    gross = _gross(
        horizons=(
            _horizon(
                (
                    _gross_snapshot(13),
                    _gross_snapshot(
                        14,
                        transition,
                        baseline_return="2",
                        candidate_return=candidate_return,
                    ),
                )
            ),
        )
    )
    row = (
        _evaluate(
            gross=gross,
            turnover=_turnover(transitions=(transition,)),
            fee_rate="0.01",
            spread_cost_rate="0",
            slippage_rate="0",
        )
        .horizons[0]
        .snapshots[0]
    )
    assert row.cost_adjusted_scenario_result == expected


@pytest.mark.parametrize("value", [True, "-0.1", "1", "NaN", "Infinity"])
def test_invalid_cost_assumptions_are_input_errors(value):
    with pytest.raises(ReplayInputError):
        _evaluate(fee_rate=value)


def test_cli_contract_and_exit_codes(monkeypatch):
    args = [
        "--candidate-id",
        "5",
        "--horizon",
        "60",
        "--fee-rate",
        "0.0005",
        "--spread-cost-rate",
        "0.0005",
        "--slippage-rate",
        "0.001",
    ]
    namespace = cli.parse_arguments(args)
    assert not hasattr(namespace, "scenario_file")
    assert not hasattr(namespace, "latest")
    with pytest.raises(SystemExit):
        cli.parse_arguments([*args, "--scenario-file", "x"])

    result = _evaluate()
    output = "\n".join(cli.report(result))
    for expected in (
        "database_write=false",
        "external_calls=false",
        "cost_model_reused=true",
        "sample_sufficiency_assessed=false",
        "cost_adjustable_forward_snapshot_ids=14",
    ):
        assert expected in output

    service = MagicMock()
    monkeypatch.setattr(
        cli, "ForwardCandidateCostAdjustedEvidenceService", lambda _session: service
    )
    service.evaluate.return_value = result
    _, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == 0
    service.evaluate.return_value = replace(
        result, status=INVALID_FORWARD_COST_ADJUSTED_EVIDENCE
    )
    _, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == 1
