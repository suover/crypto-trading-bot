from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import ResearchPolicyCandidate, StrategyReplaySnapshot
from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
    INSUFFICIENT_FORWARD_TRANSITIONS,
    INVALID_FORWARD_TURNOVER,
    NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS,
    NO_FORWARD_SNAPSHOTS,
    SUCCESS,
    ForwardCandidateTurnoverEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    BatchReplayResult,
    ReplayInputError,
    SnapshotReplayResult,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    CANDIDATE_SCHEMA_VERSION,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    TemporalRankingTurnoverService,
)
from scripts import evaluate_forward_candidate_turnover_evidence as cli


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


def _snapshot(snapshot_id=10, *, captured_at=WATERMARK_AT, top_n=7, **changes):
    data = _policy_data(top_n=top_n)
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
        "research_candidate_count": top_n,
        "prefilter_candidate_count": top_n,
        "ranked_candidate_count": top_n,
        "final_candidate_count": top_n,
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


def _replay(
    snapshot,
    baseline=None,
    scenario=None,
    *,
    status="SUCCESS",
    matches=True,
    policy=None,
    top_n=7,
):
    baseline = baseline or tuple(f"KRW-M{i}" for i in range(top_n))
    scenario = scenario or baseline
    success = status == "SUCCESS"
    return SnapshotReplayResult(
        snapshot_id=snapshot.id,
        pipeline_run_id=snapshot.pipeline_run_id,
        captured_at=snapshot.captured_at,
        dataset_schema_version=snapshot.dataset_schema_version,
        baseline_policy_signature=policy or snapshot.policy_signature,
        scenario_signature="scenario-signature" if success else None,
        status=status,
        safe_reason=None if success else "incompatible",
        rankable_candidate_count=top_n,
        stored_top_n=top_n,
        requested_top_n=top_n,
        effective_top_n=top_n if success else 0,
        held_augmented_count=0,
        baseline_matches_stored=matches,
        baseline_top_markets=baseline if success else (),
        scenario_top_markets=scenario if success else (),
        top_n_overlap_count=top_n,
        top_n_overlap_rate=Decimal("1"),
        entered_top_n=(),
        exited_top_n=(),
        candidate_results=(),
        mismatch_diagnostics=(),
    )


def _batch(rows):
    return BatchReplayResult(
        requested_snapshot_count=len(rows),
        replayed_snapshot_count=len(rows),
        compatible_snapshot_count=0,
        incompatible_snapshot_count=0,
        baseline_match_count=0,
        baseline_mismatch_count=0,
        mean_top_n_overlap_rate=None,
        mean_absolute_rank_change=None,
        total_entered_top_n=0,
        total_exited_top_n=0,
        results=tuple(rows),
    )


def _evaluate(snapshots=(), replays=None, *, candidate=None, reference=None):
    reference = reference or _snapshot()
    candidate = candidate or _candidate(reference)
    session = MagicMock()
    session.scalar.side_effect = [candidate, reference]
    session.scalars.return_value = tuple(snapshots)
    replay_service = MagicMock()
    replay_rows = tuple(replays if replays is not None else map(_replay, snapshots))
    replay_service.replay_snapshots.return_value = _batch(replay_rows)
    turnover = TemporalRankingTurnoverService(session, replay_service=replay_service)
    result = ForwardCandidateTurnoverEvidenceService(
        session, turnover_service=turnover
    ).evaluate(candidate_id=5)
    return result, session, replay_service


def test_no_forward_snapshots_is_safe_read_only():
    result, session, replay = _evaluate()
    assert result.status == NO_FORWARD_SNAPSHOTS
    assert result.transition_count == 0
    replay.replay_snapshots.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_one_forward_snapshot_has_no_synthetic_zero_transition():
    snapshot = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    result, _, replay = _evaluate((snapshot,))
    assert result.status == INSUFFICIENT_FORWARD_TRANSITIONS
    assert result.forward_timeline_snapshot_ids == (13,)
    assert result.common_replayable_snapshot_ids == (13,)
    assert result.transitions == ()
    assert result.baseline_summary.transition_count == 0
    assert result.baseline_summary.mean_replacement_rate is None
    assert result.first_forward_snapshot_has_no_prior_forward_transition is True
    assert replay.replay_snapshots.call_args.args[0] == (13,)


def test_two_and_three_forward_snapshots_create_only_adjacent_transitions():
    snapshots = tuple(
        _snapshot(i, captured_at=REGISTERED_AT + timedelta(hours=i))
        for i in (13, 14, 15)
    )
    result, _, _ = _evaluate(snapshots)
    assert result.status == SUCCESS
    assert [
        (item.baseline.previous_snapshot_id, item.baseline.current_snapshot_id)
        for item in result.transitions
    ] == [(13, 14), (14, 15)]
    assert [item.transition_index for item in result.transitions] == [1, 2]


@pytest.mark.parametrize("break_kind", ["policy", "topn", "replay", "baseline"])
def test_middle_context_or_replay_break_is_never_bridged(break_kind):
    snapshots = tuple(
        _snapshot(i, captured_at=REGISTERED_AT + timedelta(hours=i))
        for i in (13, 14, 15)
    )
    rows = [_replay(snapshot) for snapshot in snapshots]
    if break_kind == "policy":
        rows[1] = replace(rows[1], baseline_policy_signature="other-policy")
    elif break_kind == "topn":
        rows[1] = _replay(
            snapshots[1],
            baseline=tuple(f"KRW-X{i}" for i in range(5)),
            scenario=tuple(f"KRW-X{i}" for i in range(5)),
            top_n=5,
        )
    elif break_kind == "replay":
        rows[1] = _replay(snapshots[1], status="INVALID_REPLAY_DATA", matches=False)
    else:
        rows[1] = replace(rows[1], baseline_matches_stored=False)
    result, _, _ = _evaluate(snapshots, rows)
    assert result.status == INSUFFICIENT_FORWARD_TRANSITIONS
    assert result.transitions == ()
    assert result.continuity_break_count == 2


def test_no_candidate_context_replayable_snapshot_is_safe():
    snapshot = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    result, _, _ = _evaluate(
        (snapshot,), (_replay(snapshot, status="INVALID_REPLAY_DATA", matches=False),)
    )
    assert result.status == NO_COMMON_FORWARD_REPLAYABLE_SNAPSHOTS
    assert result.forward_timeline_snapshot_ids == (13,)


def test_broad_timeline_query_uses_context_and_strict_cutoffs_without_policy_filter():
    _, session, _ = _evaluate()
    statement = str(session.scalars.call_args.args[0])
    assert "strategy_replay_snapshots.id >" in statement
    assert statement.count("strategy_replay_snapshots.captured_at >") == 2
    assert "strategy_replay_snapshots.policy_signature =" not in statement
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
def test_application_validation_rejects_triple_cutoff_leaks(snapshot):
    result, _, replay = _evaluate((snapshot,))
    assert result.status == INVALID_FORWARD_TURNOVER
    assert "triple cutoff" in result.safe_reason
    replay.replay_snapshots.assert_not_called()


def test_missing_and_corrupt_candidate_fail_closed():
    session = MagicMock()
    session.scalar.return_value = None
    result = ForwardCandidateTurnoverEvidenceService(session).evaluate(candidate_id=5)
    assert result.status == INVALID_FORWARD_TURNOVER
    assert "does not exist" in result.safe_reason

    reference = _snapshot()
    result, _, _ = _evaluate(
        candidate=_candidate(reference, scenario_definition_signature="wrong"),
        reference=reference,
    )
    assert result.status == INVALID_FORWARD_TURNOVER
    assert "signature" in result.safe_reason


@pytest.mark.parametrize("replaced", range(8))
def test_top_seven_decimal_turnover_is_canonical_for_every_replacement_count(replaced):
    previous = tuple(f"M{i}" for i in range(7))
    current = (*previous[: 7 - replaced], *(f"N{i}" for i in range(replaced)))
    first = _replay(_snapshot(13), previous, previous)
    second = _replay(_snapshot(14), current, current)
    transition = TemporalRankingTurnoverService._transition(
        first, second, top_n=7, use_baseline=True
    )
    assert transition.entered_count == replaced
    assert transition.exited_count == replaced
    assert transition.replacement_rate == Decimal(replaced) / Decimal(7)
    assert transition.retention_rate == Decimal(7 - replaced) / Decimal(7)


def test_explicit_subset_reuses_batched_replay_and_rejects_invalid_ids():
    replay = MagicMock()
    replay.replay_snapshots.return_value = _batch(())
    service = TemporalRankingTurnoverService(MagicMock(), replay_service=replay)
    scenario = _scenario()
    result = service.evaluate_snapshots(scenarios=(scenario,), snapshot_ids=())
    assert result.requested_snapshot_count == 0
    replay.replay_snapshots.assert_called_once_with(
        (), overrides=dict(scenario.component_weights), top_n=None
    )
    for ids in ((0,), (True,), (1, 1)):
        with pytest.raises(ReplayInputError):
            service.evaluate_snapshots(scenarios=(scenario,), snapshot_ids=ids)


def test_cli_contract_and_exit_codes(monkeypatch):
    with pytest.raises(SystemExit):
        cli.parse_arguments(["--candidate-id", "1", "--horizon", "60"])
    with pytest.raises(SystemExit):
        cli.parse_arguments(["--candidate-id", "0"])

    snapshot = _snapshot(13, captured_at=REGISTERED_AT + timedelta(hours=1))
    result, _, _ = _evaluate((snapshot,))
    output = "\n".join(cli.report(result))
    assert "database_write=false" in output
    assert "external_calls=false" in output
    assert "outcome_data_used=false" in output
    assert "cost_data_used=false" in output
    assert "pre_registration_transition_excluded=true" in output
    assert "first_forward_snapshot_has_no_prior_forward_transition=true" in output

    service = MagicMock()
    monkeypatch.setattr(
        "scripts.evaluate_forward_candidate_turnover_evidence."
        "ForwardCandidateTurnoverEvidenceService",
        lambda _session: service,
    )
    namespace = SimpleNamespace(candidate_id=5)
    for status, exit_code in (
        (INSUFFICIENT_FORWARD_TRANSITIONS, 0),
        (INVALID_FORWARD_TURNOVER, 1),
    ):
        service.evaluate.return_value = replace(result, status=status)
        _, actual = cli.run(MagicMock(), namespace)
        assert actual == exit_code
