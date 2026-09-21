from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.candidate_registration_bounded_historical_evidence_service import (
    NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS,
    CandidateRegistrationBoundedHistoricalEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    ValidatedForwardCandidate,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from scripts.evaluate_candidate_registration_bounded_historical_evidence import (
    parse_arguments,
)


def validated_candidate() -> ValidatedForwardCandidate:
    scenario = parse_scenario_document(
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
    registered_at = datetime(2026, 9, 2, tzinfo=UTC)
    metadata = ForwardCandidateMetadata(
        candidate_id=1,
        candidate_schema_version="research-policy-candidate-v1",
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        component_weights=dict(scenario.component_weights),
        user_id=2,
        exchange="UPBIT",
        quote_asset="KRW",
        baseline_policy_signature="strategy-replay-v1:base",
        effective_top_n=7,
        dataset_schema_version="strategy-replay-dataset-v1",
        reference_snapshot_id=3,
        reference_snapshot_captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        registered_at=registered_at,
        registration_snapshot_id_watermark=10,
        registration_captured_at_watermark=registered_at,
    )
    return ValidatedForwardCandidate(
        row=SimpleNamespace(), metadata=metadata, scenario=scenario
    )


def test_historical_snapshot_query_enforces_all_registration_bounds() -> None:
    session = MagicMock()
    session.scalars.return_value = ()
    service = CandidateRegistrationBoundedHistoricalEvidenceService(session)

    assert service._load_and_validate_snapshots(validated_candidate()) == ((), ())
    sql = str(session.scalars.call_args.args[0])
    assert "strategy_replay_snapshots.id <=" in sql
    assert sql.count("strategy_replay_snapshots.captured_at <=") == 2
    assert "strategy_replay_snapshots.created_at <=" in sql
    assert "ORDER BY strategy_replay_snapshots.captured_at ASC" in sql
    assert "strategy_replay_snapshots.id ASC" in sql


def test_historical_exact_registration_boundaries_are_inclusive() -> None:
    candidate = validated_candidate()
    metadata = candidate.metadata
    snapshot = SimpleNamespace(
        id=metadata.registration_snapshot_id_watermark,
        captured_at=metadata.registration_captured_at_watermark,
        created_at=metadata.registered_at,
        policy_signature=metadata.baseline_policy_signature,
        policy_data={},
    )
    session = MagicMock()
    session.scalars.return_value = (snapshot,)
    service = CandidateRegistrationBoundedHistoricalEvidenceService(session)
    service._stored_top_n = MagicMock(return_value=metadata.effective_top_n)

    timeline, context = service._load_and_validate_snapshots(candidate)

    assert timeline == context == (snapshot,)


@pytest.mark.parametrize("violated_bound", ("id", "captured", "watermark", "created"))
def test_historical_snapshot_validation_rejects_each_post_registration_leak(
    violated_bound,
) -> None:
    candidate = validated_candidate()
    metadata = candidate.metadata
    candidate_for_test = candidate
    snapshot = SimpleNamespace(
        id=metadata.registration_snapshot_id_watermark,
        captured_at=metadata.registered_at,
        created_at=metadata.registered_at,
        policy_signature=metadata.baseline_policy_signature,
        policy_data={},
    )
    if violated_bound == "id":
        snapshot.id += 1
    elif violated_bound == "captured":
        snapshot.captured_at = metadata.registered_at + timedelta(seconds=1)
        candidate_for_test = replace(
            candidate,
            metadata=replace(
                metadata,
                registration_captured_at_watermark=metadata.registered_at
                + timedelta(seconds=2),
            ),
        )
    elif violated_bound == "watermark":
        candidate_for_test = replace(
            candidate,
            metadata=replace(
                metadata,
                registration_captured_at_watermark=metadata.registered_at
                - timedelta(seconds=1),
            ),
        )
    else:
        snapshot.created_at = metadata.registered_at + timedelta(seconds=1)
    session = MagicMock()
    session.scalars.return_value = (snapshot,)
    service = CandidateRegistrationBoundedHistoricalEvidenceService(session)
    service._stored_top_n = MagicMock(return_value=metadata.effective_top_n)

    with pytest.raises(Exception, match="post-registration snapshot leaked"):
        service._load_and_validate_snapshots(candidate_for_test)


def test_no_historical_context_is_safe_and_skips_every_upstream(monkeypatch) -> None:
    session = MagicMock()
    session.scalars.return_value = ()
    candidate = validated_candidate()
    monkeypatch.setattr(
        "crypto_trading_bot.services."
        "candidate_registration_bounded_historical_evidence_service."
        "load_and_validate_forward_candidate",
        lambda _session, _candidate_id: candidate,
    )
    services = [MagicMock() for _ in range(6)]
    result = CandidateRegistrationBoundedHistoricalEvidenceService(
        session,
        sweep_service=services[0],
        gross_robustness_service=services[1],
        turnover_service=services[2],
        cost_service=services[3],
        cost_walk_forward_service=services[4],
        cost_robustness_service=services[5],
    ).evaluate(
        candidate_id=1,
        horizons=(60,),
        initial_research_size=1,
        validation_size=1,
        fee_rate=Decimal("0.0005"),
        spread_cost_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
    )

    assert result.status == NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS
    assert result.candidate == candidate.metadata
    assert result.historical_evidence_as_of == candidate.metadata.registered_at
    assert result.database_write is result.external_calls is False
    assert all(not service.method_calls for service in services)


def test_cli_has_no_latest_or_as_of_and_requires_explicit_costs() -> None:
    parsed = parse_arguments(
        [
            "--candidate-id",
            "1",
            "--horizon",
            "60",
            "--initial-research-size",
            "2",
            "--validation-size",
            "1",
            "--fee-rate",
            "0.0005",
            "--spread-cost-rate",
            "0.0005",
            "--slippage-rate",
            "0.001",
        ]
    )
    assert parsed.candidate_id == 1
    for forbidden in ("--latest", "--as-of", "--scenario-file"):
        with pytest.raises(SystemExit):
            parse_arguments(
                [
                    "--candidate-id",
                    "1",
                    "--horizon",
                    "60",
                    "--initial-research-size",
                    "2",
                    "--validation-size",
                    "1",
                    "--fee-rate",
                    "0",
                    "--spread-cost-rate",
                    "0",
                    "--slippage-rate",
                    "0",
                    forbidden,
                    "1",
                ]
            )
