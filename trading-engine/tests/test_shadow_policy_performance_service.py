from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_performance_service import (
    INSUFFICIENT_SHADOW_TRANSITIONS,
    NO_SHADOW_ENROLLMENT,
    NO_SHADOW_EVALUATIONS,
    SUCCESS,
    ShadowPolicyPerformanceService,
)


NOW = datetime(2026, 9, 13, tzinfo=UTC)


def enrollment():
    row = SimpleNamespace(
        id=4,
        candidate_id=9,
        scenario_name="shadow",
        scenario_definition_signature="definition",
        baseline_policy_signature="baseline",
        effective_top_n=2,
        shadow_enrolled_at=NOW - timedelta(days=10),
        shadow_snapshot_id_watermark=10,
    )
    return SimpleNamespace(
        row=row, candidate=SimpleNamespace(), scenario=SimpleNamespace()
    )


def evaluation(
    snapshot_id,
    status="SUCCESS",
    *,
    baseline=("A", "B"),
    shadow=("A", "C"),
    context=True,
):
    return SimpleNamespace(
        strategy_replay_snapshot_id=snapshot_id,
        pipeline_run_id=f"pipeline-{snapshot_id}",
        snapshot_captured_at=NOW + timedelta(hours=snapshot_id),
        baseline_policy_signature="baseline",
        scenario_signature="scenario",
        replay_status="SUCCESS",
        safe_reason=None,
        baseline_matches_stored=True,
        effective_top_n=2,
        baseline_top_markets=list(baseline),
        shadow_top_markets=list(shadow),
        top_n_overlap_count=1,
        top_n_overlap_rate=Decimal("0.5"),
        entered_top_n=[shadow[-1]],
        exited_top_n=[baseline[-1]],
        evaluation_status=status,
        context_matches_enrollment=context,
    )


class StoredPerformance:
    def __init__(self):
        self.inputs = []

    def evaluate_stored_selections(self, selections, *, horizon_minutes, outcome_as_of):
        self.inputs.append((tuple(selections), horizon_minutes, outcome_as_of))
        return tuple(
            SimpleNamespace(
                snapshot_id=value.snapshot_id,
                pipeline_run_id=value.pipeline_run_id,
                captured_at=value.captured_at,
                horizon_minutes=horizon_minutes,
                baseline_policy_signature=value.baseline_policy_signature,
                scenario_signature=value.scenario_signature,
                status="SUCCESS",
                performance_evaluated=True,
                effective_top_n=value.effective_top_n,
                baseline_top_markets=value.baseline_top_markets,
                scenario_top_markets=value.scenario_top_markets,
                baseline_mean_return=Decimal("1"),
                scenario_mean_return=Decimal("2"),
                mean_return_delta=Decimal("1"),
                baseline_positive_rate=Decimal("0.5"),
                scenario_positive_rate=Decimal("1"),
                scenario_result="SCENARIO_WIN",
            )
            for value in selections
        )

    def summarize_results(self, requested_count, rows):
        return SimpleNamespace(
            successful_snapshot_count=len(rows),
            outcome_incomplete_count=0,
            invalid_outcome_count=0,
            scenario_win_count=len(rows),
            scenario_loss_count=0,
            tie_count=0,
            scenario_win_rate=Decimal("1") if rows else None,
            mean_baseline_return=Decimal("1") if rows else None,
            mean_scenario_return=Decimal("2") if rows else None,
            mean_return_delta=Decimal("1") if rows else None,
            median_snapshot_return_delta=Decimal("1") if rows else None,
            mean_baseline_positive_rate=Decimal("0.5") if rows else None,
            mean_scenario_positive_rate=Decimal("1") if rows else None,
        )


def test_no_enrollment_and_no_evaluation_are_safe_read_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_performance_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: None,
    )
    no_enrollment = ShadowPolicyPerformanceService(
        MagicMock(), now_fn=lambda: NOW
    ).evaluate(
        candidate_id=9,
        horizons=(60,),
        fee_rate="0.001",
        spread_cost_rate="0",
        slippage_rate="0",
    )
    assert no_enrollment.status == NO_SHADOW_ENROLLMENT
    assert no_enrollment.database_write is False
    assert no_enrollment.offline_replay_performed is False

    service = ShadowPolicyPerformanceService(MagicMock(), now_fn=lambda: NOW)
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_performance_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: enrollment(),
    )
    service._capture_ceiling = MagicMock(return_value=None)
    no_evaluations = service.evaluate(
        candidate_id=9,
        horizons=(60,),
        fee_rate="0.001",
        spread_cost_rate="0",
        slippage_rate="0",
    )
    assert no_evaluations.status == NO_SHADOW_EVALUATIONS
    assert no_evaluations.shadow_enrollment_verified is True
    assert no_evaluations.database_write is False


def test_frozen_timeline_drives_gross_turnover_and_cost_without_replay(
    monkeypatch,
) -> None:
    validated = enrollment()
    rows = (
        evaluation(11, baseline=("A", "B"), shadow=("A", "C")),
        evaluation(12, "CONTEXT_MISMATCH", context=False),
        evaluation(13, baseline=("A", "D"), shadow=("A", "E")),
        evaluation(14, baseline=("A", "F"), shadow=("A", "G")),
    )
    timeline = tuple((row, SimpleNamespace()) for row in rows)
    performance = StoredPerformance()
    clock_calls = []
    service = ShadowPolicyPerformanceService(
        MagicMock(),
        performance_service=performance,
        now_fn=lambda: clock_calls.append(NOW) or NOW,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_performance_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: validated,
    )
    service._capture_ceiling = MagicMock(return_value=14)
    service._load_timeline = MagicMock(return_value=timeline)
    service._validate_timeline = MagicMock()

    result = service.evaluate(
        candidate_id=9,
        horizons=(60, 240),
        fee_rate="0.001",
        spread_cost_rate="0.001",
        slippage_rate="0",
    )

    assert result.status == SUCCESS
    assert len(clock_calls) == 1
    assert result.timeline_snapshot_ids == (11, 12, 13, 14)
    assert result.successful_selection_snapshot_ids == (11, 13, 14)
    assert all(call[2] == NOW for call in performance.inputs)
    assert all(
        tuple(item.snapshot_id for item in call[0]) == (11, 13, 14)
        for call in performance.inputs
    )
    assert result.turnover.transition_count == 1
    assert result.turnover.transitions[0].previous_snapshot_id == 13
    assert result.turnover.transitions[0].current_snapshot_id == 14
    assert result.turnover.continuity_break_count == 2
    assert all(
        item.cost_adjustable_shadow_snapshot_ids == (14,)
        for item in result.cost_adjusted
    )
    assert all(
        item.cost_adjustable_coverage_rate == Decimal(1) / Decimal(3)
        for item in result.cost_adjusted
    )
    assert result.offline_replay_performed is False
    assert result.database_write is False
    assert result.external_calls is False
    assert result.policy_decision_performed is False
    assert result.promotion_performed is False
    assert result.live_policy_change is False


def test_ceiling_hook_cannot_change_current_frozen_universe(monkeypatch) -> None:
    validated = enrollment()
    original = tuple(
        (evaluation(snapshot_id), SimpleNamespace()) for snapshot_id in (11, 12)
    )
    later = original + ((evaluation(13), SimpleNamespace()),)
    service = ShadowPolicyPerformanceService(
        MagicMock(),
        performance_service=StoredPerformance(),
        now_fn=lambda: NOW,
        after_ceiling_fn=MagicMock(),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_performance_service.load_and_validate_shadow_policy_enrollment",
        lambda *_: validated,
    )
    service._capture_ceiling = MagicMock(side_effect=(12, 13))
    service._load_timeline = MagicMock(side_effect=(original, later))
    service._validate_timeline = MagicMock()

    first = service.evaluate(
        candidate_id=9,
        horizons=(60,),
        fee_rate="0",
        spread_cost_rate="0",
        slippage_rate="0",
    )
    second = service.evaluate(
        candidate_id=9,
        horizons=(60,),
        fee_rate="0",
        spread_cost_rate="0",
        slippage_rate="0",
    )

    assert first.shadow_evaluation_snapshot_id_ceiling == 12
    assert first.timeline_snapshot_ids == (11, 12)
    assert second.shadow_evaluation_snapshot_id_ceiling == 13
    assert second.timeline_snapshot_ids == (11, 12, 13)
    assert service._load_timeline.call_args_list[0].args[2] == 12
    assert service._load_timeline.call_args_list[1].args[2] == 13
    assert service.after_ceiling_fn.call_count == 2


@pytest.mark.parametrize(
    "middle_status",
    ("CONTEXT_MISMATCH", "BASELINE_INTEGRITY_FAILED", "REPLAY_INCOMPATIBLE"),
)
def test_non_success_middle_row_breaks_turnover_without_bridging(middle_status) -> None:
    service = ShadowPolicyPerformanceService(MagicMock())
    rows = (
        evaluation(11),
        evaluation(12, middle_status, context=middle_status != "CONTEXT_MISMATCH"),
        evaluation(13),
    )
    turnover = service._turnover(tuple((row, None) for row in rows))
    assert turnover.status == INSUFFICIENT_SHADOW_TRANSITIONS
    assert turnover.transition_count == 0
    assert turnover.continuity_break_count == 2


def test_naive_clock_duplicate_horizon_and_invalid_cost_fail_closed() -> None:
    with pytest.raises(ReplayInputError, match="timezone-aware"):
        ShadowPolicyPerformanceService(
            MagicMock(), now_fn=lambda: datetime(2026, 1, 1)
        ).evaluate(
            candidate_id=1,
            horizons=(60,),
            fee_rate="0",
            spread_cost_rate="0",
            slippage_rate="0",
        )
    with pytest.raises(ReplayInputError, match="duplicates"):
        ShadowPolicyPerformanceService(MagicMock()).evaluate(
            candidate_id=1,
            horizons=(60, 60),
            fee_rate="0",
            spread_cost_rate="0",
            slippage_rate="0",
        )
