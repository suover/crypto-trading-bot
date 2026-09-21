from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    HeuristicRankingWeights,
)
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    CANONICAL_COMPONENT_WEIGHT_FIELDS,
    CANONICAL_REFERENCE_FIELDS,
    CandidateGenerationError,
    DeterministicRankingCandidateGeneratorService,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    DRY_RUN,
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


NOW = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)


def _snapshot(*, weights=None, **changes):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=7,
        market_universe_prefilter_n=20,
    )
    policy_data = build_policy_data(settings, HeuristicMarketRankingPolicy(weights))
    values = {
        "id": 123,
        "analysis_run_id": 456,
        "pipeline_run_id": "reference-pipeline",
        "user_id": 3,
        "exchange": "UPBIT",
        "quote_asset": "KRW",
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "policy_signature": policy_signature(policy_data),
        "policy_data": policy_data,
        "research_candidate_count": 20,
        "prefilter_candidate_count": 20,
        "ranked_candidate_count": 7,
        "final_candidate_count": 7,
        "captured_at": NOW,
    }
    values.update(changes)
    return StrategyReplaySnapshot(**values)


def _session(snapshot=None, registered=()):
    session = MagicMock()
    session.scalar.return_value = snapshot or _snapshot()
    session.scalars.return_value = tuple(registered)
    return session


def _generate(
    *,
    snapshot=None,
    registered=(),
    step="0.05",
    max_candidates=50,
    include_registered=False,
):
    session = _session(snapshot, registered)
    result = DeterministicRankingCandidateGeneratorService(session).generate(
        reference_snapshot_id=(snapshot.id if snapshot is not None else 123),
        step=step,
        max_candidates=max_candidates,
        include_registered=include_registered,
    )
    return result, session


def test_baseline_snapshot_generates_exact_pairwise_transfers_read_only():
    result, session = _generate()
    source = result.source_component_weights
    assert result.generated_valid_count == 42
    assert result.generated_before_dedup_count == 42
    assert result.duplicate_removed_count == 0
    assert result.effective_top_n == 7
    for candidate in result.candidates:
        assert sum(candidate.component_weights.values()) == Decimal("1")
        assert all(value >= 0 for value in candidate.component_weights.values())
        changed = {
            name: candidate.component_weights[name] - source[name]
            for name in CANONICAL_COMPONENT_WEIGHT_FIELDS
            if candidate.component_weights[name] != source[name]
        }
        assert changed == {
            candidate.donor_field: Decimal("-0.05"),
            candidate.receiver_field: Decimal("0.05"),
        }
        assert (
            candidate.definition
            == parse_scenario_document(
                {
                    "schema_version": SCHEMA_VERSION,
                    "scenarios": [
                        {
                            "name": candidate.scenario_name,
                            "component_weights": candidate.component_weights,
                        }
                    ],
                }
            )[0]
        )
    assert result.database_write is False
    assert result.external_calls is False
    assert result.outcome_data_used is False
    assert result.performance_evaluated is False
    assert result.policy_decision_performed is False
    assert result.live_policy_change is False
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_custom_snapshot_policy_is_source_instead_of_hard_coded_defaults():
    custom = replace(
        HeuristicRankingWeights(),
        liquidity=Decimal("0.30"),
        trend_alignment=Decimal("0.25"),
        momentum_reference_percent=Decimal("12.5"),
    )
    snapshot = _snapshot(weights=custom)
    result, _ = _generate(snapshot=snapshot, max_candidates=1)
    assert result.source_component_weights == {
        name: getattr(custom, name) for name in CANONICAL_COMPONENT_WEIGHT_FIELDS
    }
    assert result.source_reference_parameters == {
        name: getattr(custom, name) for name in CANONICAL_REFERENCE_FIELDS
    }
    assert result.source_component_weights != {
        name: getattr(HeuristicRankingWeights(), name)
        for name in CANONICAL_COMPONENT_WEIGHT_FIELDS
    }


def test_reference_parameters_are_unchanged_by_every_generated_override():
    source = replace(
        HeuristicRankingWeights(),
        momentum_reference_percent=Decimal("11"),
        volatility_reference_percent=Decimal("22"),
        drawdown_reference_percent=Decimal("33"),
        spread_reference_rate=Decimal("0.02"),
    )
    result, _ = _generate(snapshot=_snapshot(weights=source))
    assert result.source_reference_parameters == {
        name: getattr(source, name) for name in CANONICAL_REFERENCE_FIELDS
    }
    for candidate in result.candidates:
        restored = replace(source, **candidate.component_weights)
        assert {
            name: getattr(restored, name) for name in CANONICAL_REFERENCE_FIELDS
        } == result.source_reference_parameters


def test_generation_is_deterministic_in_name_order_weights_and_signatures():
    first, _ = _generate(max_candidates=20)
    second, _ = _generate(max_candidates=20)

    def projection(result):
        return tuple(
            (
                item.scenario_name,
                item.donor_field,
                item.receiver_field,
                item.component_weights,
                item.scenario_definition_signature,
            )
            for item in result.candidates
        )

    assert projection(first) == projection(second)


def test_default_cap_balances_all_available_donors_round_robin():
    result, _ = _generate(max_candidates=20)
    counts = Counter(item.donor_field for item in result.candidates)
    assert result.generated_valid_count == 42
    assert result.returned_candidate_count == 20
    assert result.candidate_cap_applied is True
    assert set(counts) == set(CANONICAL_COMPONENT_WEIGHT_FIELDS)
    assert [counts[name] for name in CANONICAL_COMPONENT_WEIGHT_FIELDS] == [
        3,
        3,
        3,
        3,
        3,
        3,
        2,
    ]
    assert max(counts.values()) - min(counts.values()) <= 1


def test_extreme_caps_follow_canonical_donor_round_robin_order():
    one, _ = _generate(max_candidates=1)
    three, _ = _generate(max_candidates=3)
    assert [item.donor_field for item in one.candidates] == [
        CANONICAL_COMPONENT_WEIGHT_FIELDS[0]
    ]
    assert [item.donor_field for item in three.candidates] == list(
        CANONICAL_COMPONENT_WEIGHT_FIELDS[:3]
    )


def test_balanced_cap_preserves_each_transfer_identity():
    full, _ = _generate(max_candidates=50)
    capped, _ = _generate(max_candidates=20)
    full_by_transfer = {
        (item.donor_field, item.receiver_field): item for item in full.candidates
    }
    for selected in capped.candidates:
        original = full_by_transfer[(selected.donor_field, selected.receiver_field)]
        assert (
            selected.scenario_name,
            selected.component_weights,
            selected.scenario_definition_signature,
        ) == (
            original.scenario_name,
            original.component_weights,
            original.scenario_definition_signature,
        )


def test_registered_candidates_do_not_consume_default_novel_cap():
    full, _ = _generate(max_candidates=50, include_registered=True)
    registered = [
        item.scenario_definition_signature for item in full.all_candidates[:5]
    ]
    result, _ = _generate(
        registered=registered,
        max_candidates=20,
        include_registered=False,
    )
    assert result.already_registered_count == 5
    assert result.novel_candidate_count == 37
    assert result.returned_candidate_count == 20
    assert all(not item.already_registered for item in result.candidates)
    assert len({item.scenario_definition_signature for item in result.candidates}) == 20


def test_include_registered_balances_full_pool_and_is_deterministic():
    full, _ = _generate(max_candidates=50, include_registered=True)
    registered = [
        item.scenario_definition_signature for item in full.all_candidates[:5]
    ]
    first, _ = _generate(
        registered=registered,
        max_candidates=20,
        include_registered=True,
    )
    second, _ = _generate(
        registered=registered,
        max_candidates=20,
        include_registered=True,
    )
    counts = Counter(item.donor_field for item in first.candidates)
    assert first.candidates == second.candidates
    assert first.selection_includes_registered is True
    assert first.returned_candidate_count == 20
    assert any(item.already_registered for item in first.candidates)
    assert set(counts) == set(CANONICAL_COMPONENT_WEIGHT_FIELDS)
    assert max(counts.values()) - min(counts.values()) <= 1


def test_high_precision_step_still_produces_valid_bounded_names():
    step = "0.000000000012345678901234567890123456789012345678901234567890"
    result, _ = _generate(step=step, max_candidates=1)
    candidate = result.candidates[0]
    assert len(candidate.scenario_name) <= 80
    assert candidate.transfer_step == Decimal(step)
    assert (
        parse_scenario_document(
            {
                "schema_version": SCHEMA_VERSION,
                "scenarios": [
                    {
                        "name": candidate.scenario_name,
                        "component_weights": candidate.component_weights,
                    }
                ],
            }
        )[0]
        == candidate.definition
    )


def test_step_changes_definitions_and_max_candidates_caps_stable_prefix():
    small, _ = _generate(step="0.025", max_candidates=10)
    normal, _ = _generate(step="0.05", max_candidates=10)
    capped, _ = _generate(step="0.05", max_candidates=3)
    assert {item.scenario_definition_signature for item in small.candidates}.isdisjoint(
        item.scenario_definition_signature for item in normal.candidates
    )
    assert capped.candidates == normal.candidates[:3]
    assert capped.generated_valid_count == 42
    assert capped.returned_candidate_count == 3


@pytest.mark.parametrize(
    "step", [0, "0", "-0.01", "NaN", "Infinity", "0.1001", True, "x"]
)
def test_invalid_steps_are_rejected(step):
    with pytest.raises(CandidateGenerationError, match="step"):
        _generate(step=step)


@pytest.mark.parametrize("value", [0, -1, 51, True, "20", None])
def test_invalid_max_candidates_are_rejected(value):
    with pytest.raises(CandidateGenerationError, match="max_candidates"):
        _generate(max_candidates=value)


def test_insufficient_donor_is_skipped_without_failing_other_transfers():
    weights = HeuristicRankingWeights(
        liquidity=Decimal("0.70"),
        trend_alignment=Decimal("0.20"),
        momentum=Decimal("0.10"),
        volume_confirmation=Decimal("0"),
        spread=Decimal("0"),
        volatility=Decimal("0"),
        drawdown=Decimal("0"),
    )
    result, _ = _generate(
        snapshot=_snapshot(weights=weights), step="0.10", max_candidates=10
    )
    counts = Counter(item.donor_field for item in result.candidates)
    assert result.generated_valid_count == 18
    assert result.returned_candidate_count == 10
    assert set(counts) == {"liquidity", "trend_alignment", "momentum"}
    assert [counts[name] for name in ("liquidity", "trend_alignment", "momentum")] == [
        4,
        3,
        3,
    ]


def test_registered_definition_is_marked_and_zero_novel_is_normal():
    initial, _ = _generate(max_candidates=50, include_registered=True)
    signatures = [item.scenario_definition_signature for item in initial.all_candidates]
    result, _ = _generate(registered=signatures, max_candidates=20)
    assert result.already_registered_count == 42
    assert result.novel_candidate_count == 0
    assert result.returned_candidate_count == 0
    assert result.candidates == ()
    assert all(item.already_registered for item in result.all_candidates)


def test_generated_definition_is_compatible_with_registry_preview():
    snapshot = _snapshot()
    generated, _ = _generate(snapshot=snapshot, max_candidates=1)
    registry_session = _session(snapshot)
    registry_session.execute.return_value.one.return_value = (
        snapshot.id,
        snapshot.captured_at,
    )
    preview = ResearchPolicyCandidateRegistryService(registry_session).preview(
        reference_snapshot_id=snapshot.id,
        scenario=generated.candidates[0].definition,
    )
    assert preview.registration_status == DRY_RUN
    assert preview.plan.scenario_definition_signature == (
        generated.candidates[0].scenario_definition_signature
    )
    registry_session.add.assert_not_called()
    registry_session.flush.assert_not_called()
    registry_session.commit.assert_not_called()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dataset_schema_version": "old"}, "schema"),
        ({"user_id": 0}, "context"),
        ({"exchange": ""}, "context"),
        ({"quote_asset": ""}, "context"),
        ({"policy_signature": "wrong"}, "signature"),
    ],
)
def test_invalid_reference_snapshot_fails_closed(changes, message):
    with pytest.raises(CandidateGenerationError, match=message):
        _generate(snapshot=_snapshot(**changes))


def test_unsupported_ranking_policy_fails_closed_with_explicit_status():
    snapshot = _snapshot()
    policy_data = {
        **snapshot.policy_data,
        "ranking": {
            **snapshot.policy_data["ranking"],
            "policy_name": "AnotherPolicy",
        },
    }
    snapshot.policy_data = policy_data
    snapshot.policy_signature = policy_signature(policy_data)
    with pytest.raises(CandidateGenerationError, match="UNSUPPORTED_RANKING_POLICY"):
        _generate(snapshot=snapshot)


def test_source_weight_fixture_is_internally_complete():
    assert set(asdict(HeuristicRankingWeights())) == set(
        CANONICAL_COMPONENT_WEIGHT_FIELDS
    ) | set(CANONICAL_REFERENCE_FIELDS)
