from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ALREADY_REGISTERED,
    CANDIDATE_SCHEMA_VERSION,
    CREATED,
    DRY_RUN,
    ResearchPolicyCandidateConflictError,
    ResearchPolicyCandidateInputError,
)
from scripts import register_research_policy_candidate as cli


NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _result(status=CREATED):
    existing = status == ALREADY_REGISTERED
    created = status == CREATED
    return SimpleNamespace(
        candidate=SimpleNamespace(id=9) if created or existing else None,
        registration_created=created,
        registration_status=status,
        plan=SimpleNamespace(
            candidate_schema_version=CANDIDATE_SCHEMA_VERSION,
            user_id=2,
            exchange="UPBIT",
            quote_asset="KRW",
            scenario_name="momentum",
            scenario_definition_signature="ranking-scenario-definition-v1:abc",
            component_weights={"liquidity": "0.2", "momentum": "0.3"},
            reference_snapshot_id=10,
            reference_snapshot_captured_at=NOW,
            dataset_schema_version="strategy-replay-dataset-v1",
            baseline_policy_signature="strategy-replay-v1:baseline",
            effective_top_n=7,
            registered_at=NOW if created or existing else None,
            registration_snapshot_id_watermark=12,
            registration_captured_at_watermark=NOW,
        ),
    )


def test_arguments_default_to_dry_run_and_do_not_allow_registered_at():
    parsed = cli.parse_arguments(
        [
            "--scenario-file",
            "scenarios.json",
            "--scenario-name",
            "momentum",
            "--reference-snapshot-id",
            "10",
        ]
    )
    assert parsed.apply is False
    assert not hasattr(parsed, "registered_at")
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(
            [
                "--scenario-file",
                "x",
                "--scenario-name",
                "x",
                "--reference-snapshot-id",
                "0",
            ]
        )
    assert error.value.code == 2


def test_dry_run_validates_and_rolls_back_without_registration_write(monkeypatch):
    session = MagicMock()
    scenario = object()
    service = MagicMock()
    service.preview.return_value = _result(DRY_RUN)
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (scenario,))
    monkeypatch.setattr(cli, "select_scenario", lambda values, name: scenario)
    monkeypatch.setattr(
        cli, "ResearchPolicyCandidateRegistryService", lambda _: service
    )
    namespace = SimpleNamespace(
        scenario_file="x",
        scenario_name="momentum",
        reference_snapshot_id=10,
        apply=False,
    )
    lines, exit_code = cli.run(session, namespace)
    service.preview.assert_called_once_with(reference_snapshot_id=10, scenario=scenario)
    service.register.assert_not_called()
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    assert exit_code == 0
    assert "registration_status=DRY_RUN" in lines
    assert "registration_performed=false" in lines
    assert "database_write=false" in lines
    assert "candidate_id=None" in lines
    assert "registered_at=None" in lines
    assert "future_only_anchor_created=false" in lines


@pytest.mark.parametrize("status", [CREATED, ALREADY_REGISTERED])
def test_apply_commits_created_or_idempotent_registration(monkeypatch, status):
    session = MagicMock()
    scenario = object()
    service = MagicMock()
    service.register.return_value = _result(status)
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (scenario,))
    monkeypatch.setattr(cli, "select_scenario", lambda values, name: scenario)
    monkeypatch.setattr(
        cli, "ResearchPolicyCandidateRegistryService", lambda _: service
    )
    namespace = SimpleNamespace(
        scenario_file="x",
        scenario_name="momentum",
        reference_snapshot_id=10,
        apply=True,
    )
    lines, exit_code = cli.run(session, namespace)
    service.register.assert_called_once_with(
        reference_snapshot_id=10, scenario=scenario
    )
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    assert exit_code == 0
    assert f"registration_status={status}" in lines
    assert "database_write=true" in lines
    assert "candidate_id=9" in lines
    assert "future_only_anchor_created=true" in lines


def test_report_is_deterministic_explicit_and_has_no_forward_claim():
    lines = cli.report(_result(), apply=True)
    assert lines == cli.report(_result(), apply=True)
    assert lines[:13] == [
        "report_type=RESEARCH_POLICY_CANDIDATE_REGISTRATION",
        "result_type=IMMUTABLE_RESEARCH_POLICY_CANDIDATE_REGISTRATION",
        "research_only=true",
        "candidate_registration=true",
        "registration_performed=true",
        "database_write=true",
        "external_calls=false",
        "live_policy_change=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        "promotion_performed=false",
        "shadow_policy_created=false",
        "registration_status=CREATED",
    ]
    assert "forward_evidence_generated=false" in lines
    assert "forward_validation_performed=false" in lines
    assert (
        "forward_snapshot_rule=snapshot_id > registration_snapshot_id_watermark "
        "AND captured_at > registered_at AND captured_at > "
        "registration_captured_at_watermark"
    ) in lines
    assert not any(line.startswith("strict_unseen_validation=") for line in lines)


@pytest.mark.parametrize(
    ("error", "expected_exit", "message"),
    [
        (ResearchPolicyCandidateInputError("bad input"), 2, "input rejected"),
        (
            ResearchPolicyCandidateConflictError("name conflict"),
            1,
            "registration rejected",
        ),
    ],
)
def test_main_maps_input_and_registration_errors(
    monkeypatch, capsys, error, expected_exit, message
):
    monkeypatch.setattr(cli, "parse_arguments", lambda _: SimpleNamespace())
    monkeypatch.setattr(
        cli,
        "run",
        MagicMock(side_effect=error),
    )
    session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    monkeypatch.setattr(
        "crypto_trading_bot.db.database.SessionLocal", lambda: session_context
    )
    assert cli.main([]) == expected_exit
    assert message in capsys.readouterr().out
    session.rollback.assert_called_once_with()
