from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import ResearchPolicyCandidate, StrategyReplaySnapshot
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioDefinition,
    ScenarioDefinitionError,
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ALREADY_REGISTERED,
    CANDIDATE_SCHEMA_VERSION,
    CREATED,
    DRY_RUN,
    ResearchPolicyCandidateConflictError,
    ResearchPolicyCandidateInputError,
    ResearchPolicyCandidateRegistrationError,
    ResearchPolicyCandidateRegistryService,
    restore_component_weights,
    select_scenario,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


NOW = datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)


def _scenarios():
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "liquidity",
                    "component_weights": {
                        "liquidity": "0.35",
                        "trend_alignment": "0.20",
                        "momentum": "0.15",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
                {
                    "name": "momentum",
                    "component_weights": {
                        "liquidity": "0.20",
                        "trend_alignment": "0.20",
                        "momentum": "0.30",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
            ],
        }
    )


def _snapshot(**changes):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=7,
        market_universe_prefilter_n=10,
    )
    policy_data = build_policy_data(settings, HeuristicMarketRankingPolicy())
    values = {
        "id": 10,
        "analysis_run_id": 20,
        "pipeline_run_id": "pipeline-reference",
        "user_id": 3,
        "exchange": "UPBIT",
        "quote_asset": "KRW",
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "policy_signature": policy_signature(policy_data),
        "policy_data": policy_data,
        "research_candidate_count": 10,
        "prefilter_candidate_count": 10,
        "ranked_candidate_count": 7,
        "final_candidate_count": 7,
        "captured_at": NOW - timedelta(hours=1),
    }
    values.update(changes)
    return StrategyReplaySnapshot(**values)


def _session(snapshot=None, existing=(), watermark=None):
    session = MagicMock()
    session.scalar.return_value = snapshot or _snapshot()
    session.scalars.return_value = existing
    session.execute.return_value.one.return_value = watermark or (
        12,
        NOW - timedelta(minutes=1),
    )
    return session


def _candidate(snapshot=None, scenario=None, **changes):
    snapshot = snapshot or _snapshot()
    scenario = scenario or _scenarios()[0]
    values = {
        "id": 91,
        "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
        "user_id": snapshot.user_id,
        "exchange": snapshot.exchange,
        "quote_asset": snapshot.quote_asset,
        "scenario_name": scenario.name,
        "scenario_definition_signature": scenario.definition_signature,
        "component_weights": {
            name: format(value, "f")
            for name, value in scenario.component_weights.items()
        },
        "reference_snapshot_id": snapshot.id,
        "reference_snapshot_captured_at": snapshot.captured_at,
        "dataset_schema_version": snapshot.dataset_schema_version,
        "baseline_policy_signature": snapshot.policy_signature,
        "effective_top_n": 7,
        "registered_at": NOW,
        "registration_snapshot_id_watermark": 12,
        "registration_captured_at_watermark": NOW - timedelta(minutes=1),
    }
    values.update(changes)
    return ResearchPolicyCandidate(**values)


def test_scenario_selection_is_exact_and_reuses_parser_validation():
    scenarios = _scenarios()
    assert select_scenario(scenarios, "momentum") is scenarios[1]
    with pytest.raises(ResearchPolicyCandidateInputError):
        select_scenario(scenarios, "missing")
    with pytest.raises(ScenarioDefinitionError):
        parse_scenario_document(
            {
                "schema_version": "ranking-scenario-sweep-v1",
                "scenarios": [
                    {
                        "name": "invalid",
                        "component_weights": {"liquidity": "NaN"},
                    }
                ],
            }
        )


def test_raw_constructed_invalid_scenario_cannot_bypass_parser():
    valid = _scenarios()[0]
    invalid = RankingScenarioDefinition(
        name=valid.name,
        component_weights={**valid.component_weights, "liquidity": Decimal("0.99")},
        definition_signature=valid.definition_signature,
    )
    with pytest.raises(ResearchPolicyCandidateInputError):
        ResearchPolicyCandidateRegistryService(_session()).preview(
            reference_snapshot_id=10, scenario=invalid
        )


def test_valid_registration_is_immutable_context_from_reference():
    snapshot = _snapshot()
    scenario = _scenarios()[1]
    session = _session(snapshot)
    result = ResearchPolicyCandidateRegistryService(
        session, now_fn=lambda: NOW
    ).register(reference_snapshot_id=snapshot.id, scenario=scenario)
    candidate = result.candidate
    assert result.registration_status == CREATED
    assert result.registration_created is True
    assert candidate is not None
    assert candidate.candidate_schema_version == CANDIDATE_SCHEMA_VERSION
    assert candidate.registered_at == NOW
    assert candidate.registered_at.tzinfo is UTC
    assert (
        candidate.user_id,
        candidate.exchange,
        candidate.quote_asset,
        candidate.baseline_policy_signature,
        candidate.effective_top_n,
    ) == (3, "UPBIT", "KRW", snapshot.policy_signature, 7)
    assert candidate.reference_snapshot_id == snapshot.id
    assert candidate.reference_snapshot_captured_at == snapshot.captured_at
    assert candidate.scenario_name == scenario.name
    assert candidate.scenario_definition_signature == scenario.definition_signature
    assert restore_component_weights(candidate.component_weights) == (
        scenario.component_weights
    )
    assert candidate.registration_snapshot_id_watermark == 12
    assert candidate.registration_captured_at_watermark == NOW - timedelta(minutes=1)
    session.add.assert_called_once_with(candidate)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()


def test_dry_run_validates_and_calculates_plan_without_write_or_clock_claim():
    session = _session()
    now_fn = MagicMock(
        side_effect=AssertionError("dry-run must not create registered_at")
    )
    result = ResearchPolicyCandidateRegistryService(session, now_fn=now_fn).preview(
        reference_snapshot_id=10, scenario=_scenarios()[0]
    )
    assert result.registration_status == DRY_RUN
    assert result.registration_created is False
    assert result.candidate is None
    assert result.plan.registered_at is None
    assert result.plan.registration_snapshot_id_watermark == 12
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    now_fn.assert_not_called()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dataset_schema_version": "unsupported"}, "schema"),
        ({"policy_data": None}, "schema"),
        ({"policy_signature": "wrong"}, "signature"),
        ({"captured_at": datetime(2026, 1, 1)}, "timezone-aware"),
    ],
)
def test_invalid_reference_snapshot_fails_closed(changes, message):
    with pytest.raises(ResearchPolicyCandidateRegistrationError, match=message):
        ResearchPolicyCandidateRegistryService(_session(_snapshot(**changes))).preview(
            reference_snapshot_id=10, scenario=_scenarios()[0]
        )


def test_missing_reference_snapshot_is_registration_error():
    session = _session()
    session.scalar.return_value = None
    with pytest.raises(
        ResearchPolicyCandidateRegistrationError, match="does not exist"
    ):
        ResearchPolicyCandidateRegistryService(session).preview(
            reference_snapshot_id=10, scenario=_scenarios()[0]
        )


@pytest.mark.parametrize("top_n", [0, True, "7"])
def test_invalid_stored_top_n_fails_closed(top_n):
    snapshot = _snapshot()
    policy_data = {
        **snapshot.policy_data,
        "market_universe": {
            **snapshot.policy_data["market_universe"],
            "top_n": top_n,
        },
    }
    snapshot = _snapshot(
        policy_data=policy_data, policy_signature=policy_signature(policy_data)
    )
    with pytest.raises(
        ResearchPolicyCandidateRegistrationError, match="ranking policy"
    ):
        ResearchPolicyCandidateRegistryService(_session(snapshot)).preview(
            reference_snapshot_id=10, scenario=_scenarios()[0]
        )


def test_invalid_ranking_policy_data_fails_closed():
    snapshot = _snapshot()
    policy_data = {
        **snapshot.policy_data,
        "ranking": {**snapshot.policy_data["ranking"], "weights": {}},
    }
    snapshot = _snapshot(
        policy_data=policy_data, policy_signature=policy_signature(policy_data)
    )
    with pytest.raises(
        ResearchPolicyCandidateRegistrationError, match="ranking policy"
    ):
        ResearchPolicyCandidateRegistryService(_session(snapshot)).preview(
            reference_snapshot_id=10, scenario=_scenarios()[0]
        )


@pytest.mark.parametrize(
    "watermark",
    [
        (9, NOW),
        (12, datetime(2026, 1, 1)),
        (12, datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_watermark_must_cover_reference_and_be_timezone_aware(watermark):
    with pytest.raises(ResearchPolicyCandidateRegistrationError, match="watermark"):
        ResearchPolicyCandidateRegistryService(_session(watermark=watermark)).preview(
            reference_snapshot_id=10, scenario=_scenarios()[0]
        )


def test_exact_duplicate_returns_original_anchor_without_clock_or_write():
    snapshot = _snapshot()
    scenario = _scenarios()[0]
    existing = _candidate(snapshot, scenario)
    session = _session(snapshot, existing=(existing,))
    now_fn = MagicMock(side_effect=AssertionError("retry must preserve clock"))
    result = ResearchPolicyCandidateRegistryService(session, now_fn=now_fn).register(
        reference_snapshot_id=10, scenario=scenario
    )
    assert result.registration_status == ALREADY_REGISTERED
    assert result.candidate is existing
    assert result.plan.registered_at == NOW
    assert result.plan.registration_snapshot_id_watermark == 12
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    now_fn.assert_not_called()


def test_concurrent_exact_duplicate_integrity_error_reloads_original_anchor():
    snapshot = _snapshot()
    scenario = _scenarios()[0]
    existing = _candidate(snapshot, scenario)
    session = _session(snapshot)
    session.scalars.side_effect = [(), (existing,)]
    session.flush.side_effect = IntegrityError("insert", {}, Exception("unique"))

    result = ResearchPolicyCandidateRegistryService(
        session, now_fn=lambda: NOW
    ).register(reference_snapshot_id=10, scenario=scenario)

    assert result.registration_status == ALREADY_REGISTERED
    assert result.registration_created is False
    assert result.candidate is existing
    assert result.plan.registered_at == existing.registered_at
    assert result.plan.registration_snapshot_id_watermark == 12
    session.add.assert_called_once()
    session.flush.assert_called_once()


def test_same_name_different_definition_is_conflict():
    snapshot = _snapshot()
    first, second = _scenarios()
    session = _session(
        snapshot, existing=(_candidate(snapshot, second, scenario_name=first.name),)
    )
    with pytest.raises(ResearchPolicyCandidateConflictError, match="name"):
        ResearchPolicyCandidateRegistryService(session).register(
            reference_snapshot_id=10, scenario=first
        )


def test_same_definition_different_alias_is_conflict():
    snapshot = _snapshot()
    scenario = _scenarios()[0]
    session = _session(
        snapshot, existing=(_candidate(snapshot, scenario, scenario_name="alias"),)
    )
    with pytest.raises(ResearchPolicyCandidateConflictError, match="another name"):
        ResearchPolicyCandidateRegistryService(session).register(
            reference_snapshot_id=10, scenario=scenario
        )


def test_naive_registration_clock_is_rejected():
    with pytest.raises(ResearchPolicyCandidateRegistrationError, match="clock"):
        ResearchPolicyCandidateRegistryService(
            _session(), now_fn=lambda: datetime(2026, 1, 1)
        ).register(reference_snapshot_id=10, scenario=_scenarios()[0])


def test_component_weights_restore_exact_decimals():
    values = {
        "a": "0.1234567890123456789012345678",
        "b": "0.8765432109876543210987654322",
    }
    assert restore_component_weights(values) == {
        "a": Decimal(values["a"]),
        "b": Decimal(values["b"]),
    }
    for invalid in (None, {"a": "NaN"}, {"a": True}):
        with pytest.raises(ResearchPolicyCandidateRegistrationError):
            restore_component_weights(invalid)
