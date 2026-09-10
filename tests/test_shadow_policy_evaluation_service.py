from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from crypto_trading_bot.analysis.market_ranking import HeuristicRankingWeights
from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.offline_strategy_replay_service import (
    BatchReplayResult,
    SnapshotReplayResult,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    parse_scenario_document,
)
from crypto_trading_bot.services.shadow_policy_evaluation_service import (
    BASELINE_INTEGRITY_FAILED,
    CONTEXT_MISMATCH,
    DRY_RUN,
    INVALID_SHADOW_EVALUATION,
    NO_NEW_SHADOW_EVALUATIONS,
    NO_POST_ENROLLMENT_SNAPSHOTS,
    NO_SHADOW_ENROLLMENT,
    REPLAY_INCOMPATIBLE,
    SUCCESS,
    ShadowPolicyEvaluationService,
    evaluation_signature,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    ValidatedShadowPolicyEnrollment,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import policy_signature
from scripts.evaluate_shadow_policy_selection import parse_arguments, report, run


NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _component_weights():
    weights = HeuristicRankingWeights()
    return {
        key: value for key, value in asdict(weights).items() if "reference" not in key
    }


def _policy_data(*, top_n=2, liquidity="0.35"):
    weights = {
        key: str(value) for key, value in asdict(HeuristicRankingWeights()).items()
    }
    weights["liquidity"] = liquidity
    if liquidity != "0.35":
        weights["momentum"] = str(Decimal("0.50") - Decimal(liquidity))
    return {
        "dataset_schema_version": "strategy-replay-dataset-v1",
        "market_universe": {"top_n": top_n},
        "ranking": {
            "policy_name": "HeuristicMarketRankingPolicy",
            "weights": weights,
        },
    }


def _validated():
    scenario = parse_scenario_document(
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [
                {"name": "shadow", "component_weights": _component_weights()}
            ],
        }
    )[0]
    base_policy = _policy_data()
    enrollment = SimpleNamespace(
        id=5,
        candidate_id=3,
        user_id=7,
        exchange="UPBIT",
        quote_asset="KRW",
        dataset_schema_version="strategy-replay-dataset-v1",
        baseline_policy_signature=policy_signature(base_policy),
        effective_top_n=2,
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        gate_decision_signature="shadow-enrollment-gate-decision-v1:abc",
        shadow_enrolled_at=NOW,
        shadow_snapshot_id_watermark=10,
        shadow_captured_at_watermark=NOW,
    )
    return ValidatedShadowPolicyEnrollment(
        row=enrollment, candidate=SimpleNamespace(candidate_id=3), scenario=scenario
    )


def _snapshot(snapshot_id=11, *, at=None, top_n=2, changed_policy=False):
    data = _policy_data(top_n=top_n, liquidity="0.25" if changed_policy else "0.35")
    return SimpleNamespace(
        id=snapshot_id,
        pipeline_run_id=f"00000000-0000-0000-0000-{snapshot_id:012d}",
        captured_at=at or NOW + timedelta(hours=snapshot_id - 10),
        dataset_schema_version="strategy-replay-dataset-v1",
        policy_signature=policy_signature(data),
        policy_data=data,
    )


def _replay(snapshot, **changes):
    values = dict(
        snapshot_id=snapshot.id,
        pipeline_run_id=snapshot.pipeline_run_id,
        captured_at=snapshot.captured_at,
        dataset_schema_version=snapshot.dataset_schema_version,
        baseline_policy_signature=snapshot.policy_signature,
        scenario_signature="offline-replay-v1:abc",
        status="SUCCESS",
        safe_reason=None,
        rankable_candidate_count=3,
        stored_top_n=2,
        requested_top_n=2,
        effective_top_n=2,
        held_augmented_count=0,
        baseline_matches_stored=True,
        baseline_top_markets=("KRW-A", "KRW-B"),
        scenario_top_markets=("KRW-B", "KRW-C"),
        top_n_overlap_count=1,
        top_n_overlap_rate=Decimal("0.5"),
        entered_top_n=("KRW-C",),
        exited_top_n=("KRW-A",),
        candidate_results=(),
        mismatch_diagnostics=(),
    )
    values.update(changes)
    return SnapshotReplayResult(**values)


def _batch(*results):
    return BatchReplayResult(
        requested_snapshot_count=len(results),
        replayed_snapshot_count=len(results),
        compatible_snapshot_count=sum(item.compatible for item in results),
        incompatible_snapshot_count=sum(not item.compatible for item in results),
        baseline_match_count=sum(item.status == "SUCCESS" for item in results),
        baseline_mismatch_count=sum(
            item.status == "BASELINE_MISMATCH" for item in results
        ),
        mean_top_n_overlap_rate=Decimal("0"),
        mean_absolute_rank_change=Decimal("0"),
        total_entered_top_n=0,
        total_exited_top_n=0,
        results=tuple(results),
    )


def _service(monkeypatch, *, snapshots=(), existing=(), replay_results=()):
    validated = _validated()
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_evaluation_service.load_and_validate_shadow_policy_enrollment",
        lambda _session, _candidate_id: validated,
    )
    session = MagicMock()
    replay_service = MagicMock()
    replay_service.replay_snapshots.return_value = _batch(*replay_results)
    service = ShadowPolicyEvaluationService(
        session, replay_service=replay_service, now_fn=lambda: NOW + timedelta(days=1)
    )
    service._ceiling = MagicMock(
        return_value=max(
            (item.id for item in snapshots),
            default=validated.row.shadow_snapshot_id_watermark,
        )
    )
    service._timeline = MagicMock(return_value=tuple(snapshots))
    service._existing = MagicMock(return_value=tuple(existing))
    return service, session, replay_service, validated


def test_no_enrollment_is_safe_and_does_not_auto_enroll(monkeypatch):
    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_evaluation_service.load_and_validate_shadow_policy_enrollment",
        lambda _session, _candidate_id: None,
    )
    session = MagicMock()
    result = ShadowPolicyEvaluationService(session).evaluate(candidate_id=3)
    assert result.status == NO_SHADOW_ENROLLMENT
    assert not result.database_write
    session.add_all.assert_not_called()


def test_no_post_enrollment_snapshot_is_safe(monkeypatch):
    service, session, replay_service, _ = _service(monkeypatch)
    result = service.evaluate(candidate_id=3)
    assert result.status == NO_POST_ENROLLMENT_SNAPSHOTS
    replay_service.replay_snapshots.assert_not_called()
    session.add_all.assert_not_called()


def test_broad_boundary_predicates_use_strict_triple_cutoff_without_policy_filter():
    validated = _validated()
    statement = select(StrategyReplaySnapshot).where(
        *ShadowPolicyEvaluationService._boundary_predicates(validated)
    )
    sql = str(statement)
    where_clause = sql.split("WHERE", maxsplit=1)[1]
    assert "strategy_replay_snapshots.id >" in sql
    assert sql.count("strategy_replay_snapshots.captured_at >") == 2
    assert "policy_signature" not in where_clause


@pytest.mark.parametrize(
    "snapshot,expected_matches,expected_reason",
    [
        (_snapshot(), True, None),
        (_snapshot(changed_policy=True), False, "BASELINE_POLICY_CHANGED"),
        (_snapshot(top_n=3), False, "EFFECTIVE_TOP_N_CHANGED"),
        (
            _snapshot(top_n=3, changed_policy=True),
            False,
            "BASELINE_POLICY_AND_EFFECTIVE_TOP_N_CHANGED",
        ),
    ],
)
def test_context_classification(snapshot, expected_matches, expected_reason):
    result = ShadowPolicyEvaluationService._validate_snapshot(snapshot, _validated())
    assert result[:2] == (expected_matches, expected_reason)


def test_pending_candidate_context_is_one_batch_and_mismatch_is_marker(monkeypatch):
    candidate = _snapshot(11)
    mismatch = _snapshot(12, changed_policy=True)
    service, session, replay_service, _ = _service(
        monkeypatch,
        snapshots=(candidate, mismatch),
        replay_results=(_replay(candidate),),
    )
    result = service.preview(candidate_id=3)
    assert result.status == DRY_RUN
    assert result.timeline_snapshot_ids == (11, 12)
    assert result.candidate_context_snapshot_ids == (11,)
    replay_service.replay_snapshots.assert_called_once()
    assert replay_service.replay_snapshots.call_args.args == ((11,),)
    assert [row.evaluation_status for row in result.evaluations] == [
        SUCCESS,
        CONTEXT_MISMATCH,
    ]
    session.add_all.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    assert not result.outcome_data_used
    assert not result.performance_evaluated
    assert not result.policy_decision_performed
    assert not result.shadow_runtime_enabled
    assert not result.promotion_performed
    assert not result.external_calls
    assert not result.live_policy_change


@pytest.mark.parametrize(
    "replay_changes,expected",
    [
        (
            {
                "status": "BASELINE_MISMATCH",
                "baseline_matches_stored": False,
                "safe_reason": "stored ranking was not reproduced",
            },
            BASELINE_INTEGRITY_FAILED,
        ),
        (
            {"baseline_matches_stored": False},
            BASELINE_INTEGRITY_FAILED,
        ),
        (
            {
                "status": "INVALID_REPLAY_DATA",
                "baseline_matches_stored": False,
                "scenario_signature": None,
                "stored_top_n": None,
                "requested_top_n": None,
                "effective_top_n": 0,
                "baseline_top_markets": (),
                "scenario_top_markets": (),
                "top_n_overlap_count": 0,
                "top_n_overlap_rate": Decimal("0"),
                "entered_top_n": (),
                "exited_top_n": (),
                "safe_reason": "invalid replay",
            },
            REPLAY_INCOMPATIBLE,
        ),
        (
            {
                "status": "UNSUPPORTED_RANKING_POLICY",
                "baseline_matches_stored": False,
                "scenario_signature": None,
                "stored_top_n": None,
                "requested_top_n": None,
                "effective_top_n": 0,
                "baseline_top_markets": (),
                "scenario_top_markets": (),
                "top_n_overlap_count": 0,
                "top_n_overlap_rate": Decimal("0"),
                "entered_top_n": (),
                "exited_top_n": (),
                "safe_reason": "unsupported replay",
            },
            REPLAY_INCOMPATIBLE,
        ),
    ],
)
def test_replay_status_is_normalized_and_preserved(
    monkeypatch, replay_changes, expected
):
    snapshot = _snapshot()
    replay = _replay(snapshot, **replay_changes)
    service, _, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(replay,)
    )
    result = service.preview(candidate_id=3)
    assert result.status == DRY_RUN
    assert result.evaluations[0].evaluation_status == expected
    assert result.evaluations[0].replay_status == replay.status
    assert result.evaluations[0].safe_reason == replay.safe_reason


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_id": 99},
        {"pipeline_run_id": "wrong"},
        {"effective_top_n": 1},
        {"baseline_top_markets": ("KRW-A",)},
        {"baseline_top_markets": ("KRW-A", "KRW-A")},
        {"scenario_top_markets": ("KRW-C", "KRW-C")},
        {"top_n_overlap_count": 2},
        {"top_n_overlap_rate": Decimal("1")},
        {"entered_top_n": ("KRW-A",)},
        {"exited_top_n": ("KRW-C",)},
    ],
)
def test_success_structural_contradiction_invalidates_whole_batch(monkeypatch, changes):
    snapshot = _snapshot()
    service, session, _, _ = _service(
        monkeypatch,
        snapshots=(snapshot,),
        replay_results=(_replay(snapshot, **changes),),
    )
    result = service.evaluate(candidate_id=3)
    assert result.status == INVALID_SHADOW_EVALUATION
    session.add_all.assert_not_called()


def test_existing_valid_evaluation_is_not_replayed_or_updated(monkeypatch):
    snapshot = _snapshot()
    first, _, _, validated = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    existing = first.preview(candidate_id=3).evaluations[0]
    service, session, replay_service, _ = _service(
        monkeypatch, snapshots=(snapshot,), existing=(existing,)
    )
    result = service.evaluate(candidate_id=3)
    assert result.status == NO_NEW_SHADOW_EVALUATIONS
    replay_service.replay_snapshots.assert_not_called()
    session.add_all.assert_not_called()
    service._validate_existing(
        existing,
        snapshot,
        service._validate_snapshot(snapshot, validated),
        validated,
    )


def test_existing_evaluation_outside_current_shadow_boundary_is_invalid(monkeypatch):
    snapshot = _snapshot()
    first, _, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    existing = first.preview(candidate_id=3).evaluations[0]
    service, session, replay_service, _ = _service(
        monkeypatch, snapshots=(), existing=(existing,)
    )

    result = service.evaluate(candidate_id=3)

    assert result.status == INVALID_SHADOW_EVALUATION
    replay_service.replay_snapshots.assert_not_called()
    session.add_all.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("evaluation_signature", "corrupt"),
        ("candidate_id", 99),
        ("gate_decision_signature", "corrupt"),
        ("pipeline_run_id", "corrupt"),
        ("snapshot_policy_signature", "corrupt"),
    ],
)
def test_existing_corruption_is_invalid(monkeypatch, field, value):
    snapshot = _snapshot()
    first, _, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    existing = first.preview(candidate_id=3).evaluations[0]
    setattr(existing, field, value)
    service, session, replay_service, _ = _service(
        monkeypatch, snapshots=(snapshot,), existing=(existing,)
    )
    result = service.evaluate(candidate_id=3)
    assert result.status == INVALID_SHADOW_EVALUATION
    replay_service.replay_snapshots.assert_not_called()
    session.add_all.assert_not_called()


def test_signature_is_deterministic_sensitive_and_timezone_normalized(monkeypatch):
    snapshot = _snapshot()
    service, _, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    row = service.preview(candidate_id=3).evaluations[0]
    assert evaluation_signature(row) == row.evaluation_signature
    original = row.evaluation_signature
    row.snapshot_captured_at = row.snapshot_captured_at.astimezone(
        timezone(timedelta(hours=9))
    )
    assert evaluation_signature(row) == original
    for field, value in (
        ("scenario_signature", "changed"),
        ("shadow_top_markets", ["KRW-A", "KRW-C"]),
        ("evaluation_status", BASELINE_INTEGRITY_FAILED),
        ("evaluated_at", row.evaluated_at + timedelta(seconds=1)),
    ):
        changed = SimpleNamespace(**_evaluation_values(row))
        setattr(changed, field, value)
        assert evaluation_signature(changed) != original


def _evaluation_values(row):
    return {
        key: value
        for key, value in row.__dict__.items()
        if not key.startswith("_sa_") and key != "evaluation_signature"
    }


def test_apply_flushes_only_after_plan_and_never_commits(monkeypatch):
    snapshot = _snapshot()
    service, session, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    result = service.evaluate(candidate_id=3)
    assert result.status == SUCCESS
    assert result.created_evaluation_count == 1
    assert result.database_write
    session.add_all.assert_called_once()
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()


def test_ceiling_callback_does_not_change_current_invocation(monkeypatch):
    snapshot = _snapshot(11)
    events = []
    service, _, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    service.after_ceiling_fn = lambda: events.append("new-snapshot-12")
    result = service.preview(candidate_id=3)
    assert result.evaluation_snapshot_id_ceiling == 11
    assert result.timeline_snapshot_ids == (11,)
    assert events == ["new-snapshot-12"]


def test_cli_surface_and_success_commit(monkeypatch):
    assert vars(parse_arguments(["--candidate-id", "3"])) == {
        "candidate_id": 3,
        "apply": False,
    }
    with pytest.raises(SystemExit):
        parse_arguments(["--candidate-id", "3", "--weights", "x"])
    snapshot = _snapshot()
    service, _, _, _ = _service(
        monkeypatch, snapshots=(snapshot,), replay_results=(_replay(snapshot),)
    )
    result = service.evaluate(candidate_id=3)
    fake_session = MagicMock()
    monkeypatch.setattr(
        "scripts.evaluate_shadow_policy_selection.ShadowPolicyEvaluationService.evaluate",
        lambda _self, candidate_id: result,
    )
    lines, exit_code = run(
        fake_session, parse_arguments(["--candidate-id", "3", "--apply"])
    )
    assert exit_code == 0
    assert "report_type=SHADOW_SELECTION_EVALUATION" in lines
    fake_session.commit.assert_called_once_with()
    output = "\n".join(report(result))
    assert "shadow_runtime_enabled=false" in output
    assert "outcome_data_used=false" in output
