from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    INVALID_FORWARD_EVIDENCE,
    SUCCESS,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from scripts import evaluate_forward_candidate_gross_evidence as cli


NOW = datetime(2060, 1, 1, tzinfo=UTC)


def _result(status=SUCCESS):
    invalid = status == INVALID_FORWARD_EVIDENCE
    candidate = (
        None
        if invalid
        else SimpleNamespace(
            candidate_schema_version="research-policy-candidate-v1",
            scenario_name="candidate-a",
            scenario_definition_signature="ranking-scenario-definition-v1:abc",
            component_weights={"liquidity": Decimal("0.2")},
            user_id=2,
            exchange="UPBIT",
            quote_asset="KRW",
            baseline_policy_signature="strategy-replay-v1:abc",
            effective_top_n=7,
            registered_at=NOW,
            registration_snapshot_id_watermark=12,
            registration_captured_at_watermark=NOW,
        )
    )
    snapshot = SimpleNamespace(
        snapshot_id=13,
        pipeline_run_id="pipeline-13",
        captured_at=NOW,
        horizon_minutes=60,
        status="SUCCESS",
        safe_reason=None,
        performance_evaluated=True,
        baseline_policy_signature="strategy-replay-v1:abc",
        scenario_signature="offline-replay-v1:abc",
        baseline_top_markets=("KRW-A",),
        scenario_top_markets=("KRW-B",),
        baseline_mean_return=Decimal("1"),
        scenario_mean_return=Decimal("2"),
        mean_return_delta=Decimal("1"),
        scenario_result="SCENARIO_WIN",
    )
    horizon = SimpleNamespace(
        horizon_minutes=60,
        eligible_forward_snapshot_count=1,
        successful_comparable_snapshot_count=1,
        outcome_incomplete_count=0,
        baseline_integrity_failed_count=0,
        replay_incompatible_count=0,
        invalid_outcome_count=0,
        scenario_win_count=1,
        scenario_loss_count=0,
        tie_count=0,
        scenario_win_rate=Decimal("1"),
        mean_baseline_return=Decimal("1"),
        mean_scenario_return=Decimal("2"),
        mean_return_delta=Decimal("1"),
        median_snapshot_return_delta=Decimal("1"),
        mean_baseline_positive_rate=Decimal("1"),
        mean_scenario_positive_rate=Decimal("1"),
        status="SUCCESS",
        safe_reason=None,
        snapshots=(snapshot,),
    )
    return SimpleNamespace(
        candidate_id=5,
        candidate=candidate,
        requested_horizons=(60,),
        eligible_forward_snapshot_ids=() if invalid else (13,),
        status=status,
        safe_reason="invalid" if invalid else None,
        candidate_registration_verified=not invalid,
        forward_anchor_enforced=not invalid,
        pre_registration_snapshots_excluded=not invalid,
        registration_time_provenance_verified=not invalid,
        future_snapshot_cutoff_verified=not invalid,
        scenario_definition_frozen_at_registration=not invalid,
        forward_evidence_generated=not invalid,
        forward_validation_performed=True,
        horizons=() if invalid else (horizon,),
    )


def test_arguments_require_candidate_and_horizon_without_scenario_file():
    parsed = cli.parse_arguments(
        ["--candidate-id", "5", "--horizon", "1440", "--horizon", "60"]
    )
    assert parsed.candidate_id == 5
    assert parsed.horizon == [1440, 60]
    assert not hasattr(parsed, "scenario_file")
    assert not hasattr(parsed, "scenario_name")
    assert not hasattr(parsed, "latest")
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--candidate-id", "0", "--horizon", "60"])
    assert error.value.code == 2


def test_report_contains_provenance_aggregate_and_snapshot_detail():
    lines = cli.report(_result())
    assert lines[:11] == [
        "report_type=FORWARD_CANDIDATE_GROSS_EVIDENCE",
        "result_type=FORWARD_ONLY_RANKING_SELECTION_GROSS_EVIDENCE",
        "performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "research_only=true",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        "promotion_performed=false",
        "shadow_policy_created=false",
    ]
    for expected in (
        "candidate_registration_verified=true",
        "forward_anchor_enforced=true",
        "pre_registration_snapshots_excluded=true",
        "scenario_definition_frozen_at_registration=true",
        "candidate_id=5",
        "horizon_minutes=60",
        "eligible_forward_snapshot_count=1",
        "successful_comparable_snapshot_count=1",
        "snapshot_id=13",
        "performance_evaluated=true",
    ):
        assert expected in lines
    assert not any(line.startswith("strict_unseen_validation=") for line in lines)


@pytest.mark.parametrize(
    ("status", "exit_code"), [(SUCCESS, 0), (INVALID_FORWARD_EVIDENCE, 1)]
)
def test_run_is_select_only_and_maps_invalid_status(monkeypatch, status, exit_code):
    session = MagicMock()
    service = MagicMock()
    service.evaluate.return_value = _result(status)
    monkeypatch.setattr(cli, "ForwardCandidateGrossEvidenceService", lambda _: service)
    namespace = SimpleNamespace(candidate_id=5, horizon=[60])
    _, actual_exit = cli.run(session, namespace)
    assert actual_exit == exit_code
    service.evaluate.assert_called_once_with(candidate_id=5, horizons=[60])
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_main_maps_input_error_to_exit_two(monkeypatch, capsys):
    monkeypatch.setattr(cli, "parse_arguments", lambda _: SimpleNamespace())
    monkeypatch.setattr(cli, "run", MagicMock(side_effect=ReplayInputError("bad")))
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock()
    monkeypatch.setattr(
        "crypto_trading_bot.db.database.SessionLocal", lambda: session_context
    )
    assert cli.main([]) == 2
    assert "rejected" in capsys.readouterr().out
