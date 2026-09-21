from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import ResearchPolicyCandidate, StrategyReplaySnapshot
from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    FORWARD_OUTCOMES_PENDING,
    INVALID_FORWARD_EVIDENCE,
    NO_COMPARABLE_FORWARD_SNAPSHOTS,
    NO_FORWARD_SNAPSHOTS,
    SUCCESS,
    ForwardCandidateGrossEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    CANDIDATE_SCHEMA_VERSION,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    BASELINE_INTEGRITY_FAILED,
    INVALID_OUTCOME_DATA,
    OUTCOME_INCOMPLETE,
    REPLAY_INCOMPATIBLE,
    StrategyABPerformanceService,
    StrategyABSnapshotPerformanceResult,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


REGISTERED_AT = datetime(2060, 1, 2, tzinfo=UTC)
WATERMARK_AT = datetime(2060, 1, 1, 12, tzinfo=UTC)


def _scenario():
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "candidate-a",
                    "component_weights": {
                        "liquidity": "0.20",
                        "trend_alignment": "0.20",
                        "momentum": "0.30",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                }
            ],
        }
    )[0]


def _policy_data(*, top_n=7):
    return build_policy_data(
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            market_universe_mode="DYNAMIC",
            market_universe_top_n=top_n,
            market_universe_prefilter_n=max(10, top_n),
        ),
        HeuristicMarketRankingPolicy(),
    )


def _snapshot(snapshot_id=10, *, captured_at=WATERMARK_AT, **changes):
    data = _policy_data()
    values = {
        "id": snapshot_id,
        "analysis_run_id": 100 + snapshot_id,
        "pipeline_run_id": f"pipeline-{snapshot_id}",
        "user_id": 3,
        "exchange": "UPBIT",
        "quote_asset": "KRW",
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "policy_signature": policy_signature(data),
        "policy_data": data,
        "research_candidate_count": 7,
        "prefilter_candidate_count": 7,
        "ranked_candidate_count": 7,
        "final_candidate_count": 7,
        "captured_at": captured_at,
    }
    values.update(changes)
    return StrategyReplaySnapshot(**values)


def _candidate(reference=None, **changes):
    reference = reference or _snapshot()
    scenario = _scenario()
    values = {
        "id": 5,
        "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
        "user_id": reference.user_id,
        "exchange": reference.exchange,
        "quote_asset": reference.quote_asset,
        "scenario_name": scenario.name,
        "scenario_definition_signature": scenario.definition_signature,
        "component_weights": {
            name: format(value, "f")
            for name, value in scenario.component_weights.items()
        },
        "reference_snapshot_id": reference.id,
        "reference_snapshot_captured_at": reference.captured_at,
        "dataset_schema_version": reference.dataset_schema_version,
        "baseline_policy_signature": reference.policy_signature,
        "effective_top_n": 7,
        "registered_at": REGISTERED_AT,
        "registration_snapshot_id_watermark": 12,
        "registration_captured_at_watermark": WATERMARK_AT,
    }
    values.update(changes)
    return ResearchPolicyCandidate(**values)


def _ab_result(snapshot, horizon, *, status="SUCCESS", delta="1"):
    success = status == "SUCCESS"
    value = Decimal(delta) if success else None
    return StrategyABSnapshotPerformanceResult(
        snapshot_id=snapshot.id,
        pipeline_run_id=snapshot.pipeline_run_id,
        captured_at=snapshot.captured_at,
        horizon_minutes=horizon,
        baseline_policy_signature=snapshot.policy_signature,
        scenario_signature="offline-replay-v1:scenario" if success else None,
        replay_status="SUCCESS",
        replay_safe_reason=None,
        status=status,
        safe_reason=None if success else "not comparable",
        performance_evaluated=success,
        effective_top_n=7 if success else 0,
        baseline_top_markets=("KRW-A",),
        scenario_top_markets=("KRW-B",),
        top_n_overlap_count=0,
        top_n_overlap_rate=Decimal("0"),
        entered_top_n=("KRW-B",),
        exited_top_n=("KRW-A",),
        baseline_required_count=1,
        scenario_required_count=1,
        baseline_complete_count=1 if success else 0,
        scenario_complete_count=1 if success else 0,
        baseline_missing_markets=() if success else ("KRW-A",),
        scenario_missing_markets=() if success else ("KRW-B",),
        baseline_candidate_count=1,
        scenario_candidate_count=1,
        baseline_mean_return=Decimal("2") if success else None,
        scenario_mean_return=(Decimal("2") + value) if success else None,
        mean_return_delta=value,
        baseline_median_return=Decimal("2") if success else None,
        scenario_median_return=(Decimal("2") + value) if success else None,
        median_return_delta=value,
        baseline_positive_count=1 if success else None,
        scenario_positive_count=1 if success else None,
        baseline_negative_count=0 if success else None,
        scenario_negative_count=0 if success else None,
        baseline_flat_count=0 if success else None,
        scenario_flat_count=0 if success else None,
        baseline_positive_rate=Decimal("1") if success else None,
        scenario_positive_rate=Decimal("1") if success else None,
        positive_rate_delta=Decimal("0") if success else None,
        scenario_result=(
            "SCENARIO_WIN"
            if success and value > 0
            else "SCENARIO_LOSS"
            if success and value < 0
            else "TIE"
            if success
            else None
        ),
    )


def _session(candidate=None, reference=None, snapshots=()):
    reference = reference or _snapshot()
    candidate = candidate or _candidate(reference)
    session = MagicMock()
    session.scalar.side_effect = [candidate, reference]
    session.scalars.return_value = tuple(snapshots)
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    return session


def _performance(result_factory=None):
    service = MagicMock()
    factory = result_factory or (
        lambda snapshot_id, horizon: _ab_result(
            _snapshot(
                snapshot_id,
                captured_at=REGISTERED_AT + timedelta(hours=snapshot_id),
            ),
            horizon,
        )
    )
    snapshots = {}

    def evaluate(snapshot_ids, *, horizon_minutes, overrides):
        return tuple(
            factory(snapshot_id, horizon_minutes) for snapshot_id in snapshot_ids
        )

    service.evaluate_snapshots.side_effect = evaluate
    service.summarize_results.side_effect = lambda requested, results: (
        StrategyABPerformanceService._summarize(requested, tuple(results))
    )
    service.snapshots = snapshots
    return service


def _evaluate(
    candidate=None, reference=None, snapshots=(), performance=None, horizons=(60,)
):
    session = _session(candidate, reference, snapshots)
    performance = performance or _performance(
        lambda snapshot_id, horizon: _ab_result(
            next(item for item in snapshots if item.id == snapshot_id), horizon
        )
    )
    result = ForwardCandidateGrossEvidenceService(
        session, performance_service=performance
    ).evaluate(candidate_id=5, horizons=horizons)
    return result, session, performance


def test_valid_candidate_no_forward_snapshots_is_safe_and_read_only():
    result, session, performance = _evaluate(horizons=(1440, 60, 60, 240))
    assert result.status == NO_FORWARD_SNAPSHOTS
    assert result.requested_horizons == (60, 240, 1440)
    assert result.candidate_registration_verified is True
    assert result.forward_anchor_enforced is True
    assert result.forward_evidence_generated is False
    assert all(item.status == NO_FORWARD_SNAPSHOTS for item in result.horizons)
    performance.evaluate_snapshots.assert_not_called()
    assert not session.new and not session.dirty and not session.deleted
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_missing_candidate_is_invalid():
    session = MagicMock()
    session.scalar.return_value = None
    result = ForwardCandidateGrossEvidenceService(session).evaluate(
        candidate_id=5, horizons=(60,)
    )
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "does not exist" in result.safe_reason


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"candidate_schema_version": "unsupported"}, "schema"),
        ({"component_weights": {"liquidity": "NaN"}}, "weights"),
        ({"scenario_definition_signature": "wrong"}, "signature"),
        ({"registered_at": datetime(2060, 1, 2)}, "timezone-aware"),
        ({"registration_snapshot_id_watermark": 0}, "positive integer"),
        ({"effective_top_n": 0}, "positive integer"),
    ],
)
def test_candidate_corruption_fails_closed(changes, reason):
    reference = _snapshot()
    result, _, _ = _evaluate(
        candidate=_candidate(reference, **changes), reference=reference
    )
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert reason in result.safe_reason


def test_missing_reference_snapshot_is_invalid():
    reference = _snapshot()
    session = MagicMock()
    session.scalar.side_effect = [_candidate(reference), None]
    result = ForwardCandidateGrossEvidenceService(session).evaluate(
        candidate_id=5, horizons=(60,)
    )
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "reference snapshot does not exist" in result.safe_reason


@pytest.mark.parametrize(
    "reference_changes",
    [
        {"user_id": 99},
        {"exchange": "BITHUMB"},
        {"quote_asset": "USDT"},
        {"dataset_schema_version": "unsupported"},
    ],
)
def test_reference_context_mismatch_is_invalid(reference_changes):
    original = _snapshot()
    changed = _snapshot(**reference_changes)
    result, _, _ = _evaluate(candidate=_candidate(original), reference=changed)
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "context mismatch" in result.safe_reason


def test_reference_policy_signature_and_topn_are_revalidated():
    original = _snapshot()
    bad_signature = _snapshot(policy_signature="wrong")
    result, _, _ = _evaluate(candidate=_candidate(original), reference=bad_signature)
    assert result.status == INVALID_FORWARD_EVIDENCE

    top5 = _policy_data(top_n=5)
    bad_topn = _snapshot(policy_data=top5, policy_signature=policy_signature(top5))
    candidate = _candidate(
        original, baseline_policy_signature=bad_topn.policy_signature
    )
    result, _, _ = _evaluate(candidate=candidate, reference=bad_topn)
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "TopN mismatch" in result.safe_reason


def test_query_contains_context_and_all_three_strict_cutoffs():
    _, session, _ = _evaluate()
    statement = str(session.scalars.call_args.args[0])
    assert "strategy_replay_snapshots.id >" in statement
    assert statement.count("strategy_replay_snapshots.captured_at >") == 2
    for field in (
        "user_id",
        "exchange",
        "quote_asset",
        "dataset_schema_version",
        "policy_signature",
    ):
        assert f"strategy_replay_snapshots.{field}" in statement
    assert "ORDER BY strategy_replay_snapshots.captured_at ASC" in statement
    assert "strategy_replay_snapshots.id ASC" in statement


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(12, captured_at=REGISTERED_AT + timedelta(hours=1)),
        _snapshot(13, captured_at=REGISTERED_AT),
        _snapshot(13, captured_at=WATERMARK_AT),
    ],
)
def test_application_layer_rejects_any_snapshot_violating_triple_cutoff(snapshot):
    result, _, performance = _evaluate(snapshots=(snapshot,))
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "triple cutoff" in result.safe_reason
    performance.evaluate_snapshots.assert_not_called()


def test_timezone_equivalent_values_pass_and_naive_snapshot_fails():
    equivalent = (REGISTERED_AT + timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=9))
    )
    snapshot = _snapshot(13, captured_at=equivalent)
    result, _, _ = _evaluate(snapshots=(snapshot,))
    assert result.status == SUCCESS

    naive = _snapshot(13, captured_at=datetime(2060, 1, 3))
    result, _, _ = _evaluate(snapshots=(naive,))
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "timezone-aware" in result.safe_reason


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": 99},
        {"exchange": "BITHUMB"},
        {"quote_asset": "USDT"},
        {"dataset_schema_version": "unsupported"},
        {"policy_signature": "different"},
    ],
)
def test_application_layer_rejects_query_context_leak(changes):
    snapshot = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1), **changes)
    result, _, _ = _evaluate(snapshots=(snapshot,))
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "context mismatch" in result.safe_reason


def test_forward_topn_mismatch_is_invalid():
    data = _policy_data(top_n=5)
    snapshot = _snapshot(
        13,
        captured_at=REGISTERED_AT + timedelta(hours=1),
        policy_data=data,
    )
    result, _, _ = _evaluate(snapshots=(snapshot,))
    assert result.status == INVALID_FORWARD_EVIDENCE


def test_chronology_and_duplicate_snapshot_id_fail_closed():
    later = _snapshot(14, captured_at=REGISTERED_AT + timedelta(hours=2))
    earlier = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    result, _, _ = _evaluate(snapshots=(later, earlier))
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "chronology" in result.safe_reason

    duplicate = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=2))
    result, _, _ = _evaluate(snapshots=(earlier, duplicate))
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "duplicate" in result.safe_reason


def test_same_captured_at_orders_by_snapshot_id_and_uses_registered_weights():
    captured_at = REGISTERED_AT + timedelta(hours=1)
    first = _snapshot(13, captured_at=captured_at)
    second = _snapshot(14, captured_at=captured_at)
    scenario = _scenario()
    result, _, performance = _evaluate(snapshots=(first, second))
    assert result.status == SUCCESS
    assert result.eligible_forward_snapshot_ids == (13, 14)
    performance.evaluate_snapshots.assert_called_once()
    call = performance.evaluate_snapshots.call_args
    assert call.args[0] == (13, 14)
    assert call.kwargs["overrides"] == scenario.component_weights
    assert performance.summarize_results.call_count == 1


def test_horizon_maturity_isolated_and_successful_subset_only():
    first = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    second = _snapshot(14, captured_at=REGISTERED_AT + timedelta(hours=2))

    def factory(snapshot_id, horizon):
        snapshot = first if snapshot_id == 13 else second
        if horizon == 60 and snapshot_id == 13:
            return _ab_result(snapshot, horizon, delta="2")
        if horizon == 60:
            return _ab_result(snapshot, horizon, status=OUTCOME_INCOMPLETE)
        if horizon == 240:
            return _ab_result(snapshot, horizon, status=OUTCOME_INCOMPLETE)
        return _ab_result(snapshot, horizon, status=REPLAY_INCOMPATIBLE)

    result, _, _ = _evaluate(
        snapshots=(first, second),
        performance=_performance(factory),
        horizons=(1440, 240, 60),
    )
    assert result.status == SUCCESS
    sixty, two_forty, daily = result.horizons
    assert sixty.status == SUCCESS
    assert sixty.successful_comparable_snapshot_count == 1
    assert sixty.outcome_incomplete_count == 1
    assert sixty.mean_return_delta == Decimal("2")
    assert two_forty.status == FORWARD_OUTCOMES_PENDING
    assert two_forty.outcome_incomplete_count == 2
    assert daily.status == NO_COMPARABLE_FORWARD_SNAPSHOTS
    assert daily.replay_incompatible_count == 2


@pytest.mark.parametrize(
    "status",
    [BASELINE_INTEGRITY_FAILED, REPLAY_INCOMPATIBLE, INVALID_OUTCOME_DATA],
)
def test_non_pending_failure_is_no_comparable(status):
    snapshot = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    performance = _performance(
        lambda snapshot_id, horizon: _ab_result(snapshot, horizon, status=status)
    )
    result, _, _ = _evaluate(snapshots=(snapshot,), performance=performance)
    assert result.status == NO_COMPARABLE_FORWARD_SNAPSHOTS


def test_invalid_ab_lineage_fails_entire_evidence_closed():
    snapshot = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    performance = _performance(
        lambda snapshot_id, horizon: _ab_result(
            snapshot, horizon, status=OUTCOME_INCOMPLETE
        )
    )
    bad = _ab_result(snapshot, 60)
    performance.evaluate_snapshots.return_value = (bad,)
    performance.evaluate_snapshots.side_effect = None
    bad = StrategyABSnapshotPerformanceResult(**{**bad.__dict__, "snapshot_id": 999})
    performance.evaluate_snapshots.return_value = (bad,)
    result, _, _ = _evaluate(snapshots=(snapshot,), performance=performance)
    assert result.status == INVALID_FORWARD_EVIDENCE
    assert "lineage mismatch" in result.safe_reason


def test_invalid_input_horizons_and_candidate_id_are_rejected():
    service = ForwardCandidateGrossEvidenceService(MagicMock())
    for horizons in ((), (0,), (True,)):
        with pytest.raises(ReplayInputError):
            service.evaluate(candidate_id=5, horizons=horizons)
    with pytest.raises(ReplayInputError):
        service.evaluate(candidate_id=0, horizons=(60,))
