from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DeterministicRankingCandidateGeneratorService,
)
from crypto_trading_bot.services.reference_bounded_historical_research_batch_service import (
    CANONICAL_HORIZONS,
    INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH,
    NO_NOVEL_CANDIDATES,
    SUCCESS,
    ReferenceBoundedHistoricalResearchBatchService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)
from scripts.evaluate_generated_candidate_historical_batch import (
    parse_arguments,
    result_document,
    write_result_file,
)


NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _snapshot(*, snapshot_id=123, captured_at=NOW, created_at=None, **changes):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=7,
        market_universe_prefilter_n=20,
    )
    policy_data = build_policy_data(settings, HeuristicMarketRankingPolicy())
    values = {
        "id": snapshot_id,
        "analysis_run_id": snapshot_id + 1000,
        "pipeline_run_id": f"pipeline-{snapshot_id}",
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
        "captured_at": captured_at,
        "created_at": created_at or captured_at,
    }
    values.update(changes)
    return StrategyReplaySnapshot(**values)


def _generation(snapshot, *, registered=()):
    session = MagicMock()
    session.scalar.return_value = snapshot
    session.scalars.return_value = tuple(registered)
    return DeterministicRankingCandidateGeneratorService(session).generate(
        reference_snapshot_id=snapshot.id
    )


def _scenario_result(candidate, *, weights=True):
    values = {
        "scenario_name": candidate.scenario_name,
        "scenario_definition_signature": candidate.scenario_definition_signature,
    }
    if weights:
        values["component_weights"] = candidate.component_weights
    return SimpleNamespace(**values)


def _pipeline_results(generation, context_ids):
    context_count = len(context_ids)
    scenarios = tuple(
        item.definition
        for item in generation.all_candidates
        if not item.already_registered
    )
    baseline = generation.reference_policy_signature
    top_n = generation.effective_top_n

    def cohorts(*, weights=True):
        return tuple(
            SimpleNamespace(
                horizon_minutes=horizon,
                baseline_policy_signature=baseline,
                effective_top_n=top_n,
                candidate_snapshot_ids=tuple(context_ids),
                status="INSUFFICIENT_TEST_DATA",
                safe_reason="fixture",
                scenario_results=tuple(
                    _scenario_result(candidate, weights=weights)
                    for candidate in generation.all_candidates
                    if not candidate.already_registered
                ),
            )
            for horizon in CANONICAL_HORIZONS
        )

    matrix = SimpleNamespace(
        requested_snapshot_count=context_count,
        evaluated_snapshot_count=context_count,
        scenarios=scenarios,
        horizons=CANONICAL_HORIZONS,
        cohorts=cohorts(),
    )
    gross = SimpleNamespace(
        requested_snapshot_count=context_count,
        scenarios=scenarios,
        horizon_count=3,
        cohorts=cohorts(),
    )
    walk = SimpleNamespace(
        scenarios=scenarios,
        horizon_count=3,
        cohorts=cohorts(),
    )
    robustness = SimpleNamespace(
        scenarios=scenarios,
        horizon_count=3,
        cohorts=cohorts(),
    )
    turnover_cohort = SimpleNamespace(
        baseline_policy_signature=baseline,
        effective_top_n=top_n,
        transition_count=1,
        continuity_break_count=0,
        scenario_summaries=tuple(
            _scenario_result(candidate, weights=False)
            for candidate in generation.all_candidates
            if not candidate.already_registered
        ),
    )
    turnover = SimpleNamespace(
        requested_snapshot_count=context_count,
        scenarios=scenarios,
        status="INSUFFICIENT_TEMPORAL_TRANSITIONS",
        safe_reason="fixture",
        cohorts=(turnover_cohort,),
    )
    cost = SimpleNamespace(
        scenarios=scenarios,
        horizon_count=3,
        status="NO_COST_ADJUSTABLE_SNAPSHOTS",
        safe_reason="fixture",
        cohorts=cohorts(),
    )
    cost_walk = SimpleNamespace(
        scenarios=scenarios,
        horizon_count=3,
        status="INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA",
        safe_reason="fixture",
        cohorts=cohorts(),
    )
    cost_robustness = SimpleNamespace(
        scenarios=scenarios,
        horizon_count=3,
        status="INSUFFICIENT_COST_ADJUSTED_WALK_FORWARD_DATA",
        safe_reason="fixture",
        cohorts=cohorts(),
    )
    return matrix, gross, walk, robustness, turnover, cost, cost_walk, cost_robustness


def _service(snapshot, generation):
    session = MagicMock()
    session.scalar.return_value = snapshot
    services = [MagicMock() for _ in range(7)]
    service = ReferenceBoundedHistoricalResearchBatchService(
        session,
        generator_service=MagicMock(generate=MagicMock(return_value=generation)),
        sweep_service=services[0],
        gross_walk_forward_service=services[1],
        gross_robustness_service=services[2],
        turnover_service=services[3],
        cost_service=services[4],
        cost_walk_forward_service=services[5],
        cost_robustness_service=services[6],
    )
    return service, session, services


def test_all_novel_candidates_ignore_default_twenty_cap_and_call_pipeline_once():
    snapshot = _snapshot()
    full = _generation(snapshot)
    registered = (full.all_candidates[0].scenario_definition_signature,)
    generation = _generation(snapshot, registered=registered)
    assert generation.generated_valid_count == 42
    assert generation.returned_candidate_count == 20
    assert generation.novel_candidate_count == 41
    service, session, services = _service(snapshot, generation)
    timeline = (
        _snapshot(snapshot_id=121, captured_at=NOW - timedelta(days=2)),
        snapshot,
    )
    service._load_and_validate_snapshots = MagicMock(return_value=(timeline, timeline))
    pipeline = _pipeline_results(generation, tuple(item.id for item in timeline))
    services[0].evaluate_matrix_snapshots.return_value = pipeline[0]
    services[0].result_from_matrix.return_value = pipeline[1]
    services[1].evaluate_from_matrix.return_value = pipeline[2]
    services[2].evaluate_from_results.return_value = pipeline[3]
    services[3].evaluate_snapshots.return_value = pipeline[4]
    services[4].evaluate_from_results.return_value = pipeline[5]
    services[5].evaluate_from_result.return_value = pipeline[6]
    services[6].evaluate_from_results.return_value = pipeline[7]

    result = service.evaluate(reference_snapshot_id=snapshot.id)

    assert result.status == SUCCESS
    assert result.generated_candidate_count == 42
    assert result.already_registered_candidate_count == 1
    assert result.novel_candidate_count == result.evaluated_candidate_count == 41
    expected = tuple(
        item.scenario_name
        for item in generation.all_candidates
        if not item.already_registered
    )
    assert tuple(item.scenario_name for item in result.candidate_results) == expected
    assert all(
        tuple(item.horizon_minutes for item in candidate.horizons) == CANONICAL_HORIZONS
        for candidate in result.candidate_results
    )
    matrix_call = services[0].evaluate_matrix_snapshots.call_args.kwargs
    assert len(matrix_call["scenarios"]) == 41
    assert matrix_call["snapshot_ids"] == (121, 123)
    assert matrix_call["outcome_as_of"] == snapshot.created_at
    services[0].evaluate_matrix_snapshots.assert_called_once()
    services[3].evaluate_snapshots.assert_called_once()
    services[4].evaluate_from_results.assert_called_once()
    services[2].evaluate_from_results.assert_called_once_with(pipeline[0], pipeline[2])
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    session.delete.assert_not_called()


def test_zero_registered_evaluates_all_42_candidates():
    snapshot = _snapshot()
    generation = _generation(snapshot)
    service, _, services = _service(snapshot, generation)
    service._load_and_validate_snapshots = MagicMock(
        return_value=((snapshot,), (snapshot,))
    )
    pipeline = _pipeline_results(generation, (snapshot.id,))
    services[0].evaluate_matrix_snapshots.return_value = pipeline[0]
    services[0].result_from_matrix.return_value = pipeline[1]
    services[1].evaluate_from_matrix.return_value = pipeline[2]
    services[2].evaluate_from_results.return_value = pipeline[3]
    services[3].evaluate_snapshots.return_value = pipeline[4]
    services[4].evaluate_from_results.return_value = pipeline[5]
    services[5].evaluate_from_result.return_value = pipeline[6]
    services[6].evaluate_from_results.return_value = pipeline[7]

    result = service.evaluate(reference_snapshot_id=snapshot.id)

    assert result.novel_candidate_count == result.evaluated_candidate_count == 42
    assert (
        len(services[0].evaluate_matrix_snapshots.call_args.kwargs["scenarios"]) == 42
    )


def test_zero_novel_candidates_skip_every_expensive_service():
    snapshot = _snapshot()
    generation = _generation(snapshot)
    all_registered = tuple(
        replace(item, already_registered=True) for item in generation.all_candidates
    )
    generation = replace(
        generation,
        all_candidates=all_registered,
        candidates=(),
        returned_candidate_count=0,
        already_registered_count=42,
        novel_candidate_count=0,
    )
    service, _, services = _service(snapshot, generation)

    result = service.evaluate(reference_snapshot_id=snapshot.id)

    assert result.status == NO_NOVEL_CANDIDATES
    assert result.novel_candidate_count == result.evaluated_candidate_count == 0
    assert result.historical_evaluation_performed is False
    assert all(not item.method_calls for item in services)


def test_snapshot_query_enforces_reference_scope_and_context_identity():
    reference = _snapshot()
    generation = _generation(reference)
    compatible = _snapshot(snapshot_id=120, captured_at=NOW - timedelta(days=1))
    different_top_n = _snapshot(
        snapshot_id=121,
        captured_at=NOW - timedelta(hours=12),
    )
    service, session, _ = _service(reference, generation)
    session.scalars.return_value = (compatible, different_top_n, reference)
    original = service._validated_snapshot_policy
    service._validated_snapshot_policy = MagicMock(
        side_effect=[
            original(compatible),
            (original(different_top_n)[0], 5),
            original(reference),
        ]
    )

    timeline, context = service._load_and_validate_snapshots(
        reference, generation, reference.captured_at, reference.created_at
    )

    assert tuple(item.id for item in timeline) == (120, 121, 123)
    assert tuple(item.id for item in context) == (120, 123)
    sql = str(session.scalars.call_args.args[0])
    assert "strategy_replay_snapshots.user_id =" in sql
    assert "strategy_replay_snapshots.exchange =" in sql
    assert "strategy_replay_snapshots.quote_asset =" in sql
    assert "strategy_replay_snapshots.dataset_schema_version =" in sql
    assert "strategy_replay_snapshots.id <=" in sql
    assert "strategy_replay_snapshots.captured_at <=" in sql
    assert "strategy_replay_snapshots.created_at <=" in sql
    assert "ORDER BY strategy_replay_snapshots.captured_at ASC" in sql
    assert "strategy_replay_snapshots.id ASC" in sql


def test_corrupted_snapshot_fails_closed_instead_of_being_skipped():
    reference = _snapshot()
    generation = _generation(reference)
    corrupted = _snapshot(snapshot_id=122, captured_at=NOW - timedelta(hours=1))
    corrupted.policy_signature = "corrupted"
    service, session, services = _service(reference, generation)
    session.scalars.return_value = (corrupted, reference)

    result = service.evaluate(reference_snapshot_id=reference.id)

    assert result.status == INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH
    assert "policy signature" in result.safe_reason
    assert all(not item.method_calls for item in services)


def test_duplicate_horizon_cohort_fails_closed():
    snapshot = _snapshot()
    generation = _generation(snapshot)
    service, _, services = _service(snapshot, generation)
    service._load_and_validate_snapshots = MagicMock(
        return_value=((snapshot,), (snapshot,))
    )
    pipeline = list(_pipeline_results(generation, (snapshot.id,)))
    pipeline[1] = SimpleNamespace(
        requested_snapshot_count=1,
        scenarios=pipeline[1].scenarios,
        horizon_count=3,
        cohorts=pipeline[1].cohorts + (pipeline[1].cohorts[0],),
    )
    services[0].evaluate_matrix_snapshots.return_value = pipeline[0]
    services[0].result_from_matrix.return_value = pipeline[1]
    services[1].evaluate_from_matrix.return_value = pipeline[2]
    services[2].evaluate_from_results.return_value = pipeline[3]
    services[3].evaluate_snapshots.return_value = pipeline[4]
    services[4].evaluate_from_results.return_value = pipeline[5]
    services[5].evaluate_from_result.return_value = pipeline[6]
    services[6].evaluate_from_results.return_value = pipeline[7]

    result = service.evaluate(reference_snapshot_id=snapshot.id)

    assert result.status == INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH
    assert "duplicate gross sweep cohort" in result.safe_reason


def test_cli_arguments_are_fixed_profile_and_have_no_candidate_cap():
    parsed = parse_arguments(["--reference-snapshot-id", "123"])
    assert parsed.reference_snapshot_id == 123
    assert parsed.step == Decimal("0.05")
    for forbidden in (
        "--max-candidates",
        "--horizon",
        "--fee-rate",
        "--validation-size",
    ):
        with pytest.raises(SystemExit):
            parse_arguments(["--reference-snapshot-id", "123", forbidden, "1"])


def test_result_json_preserves_decimal_and_aware_datetime_strings(tmp_path):
    snapshot = _snapshot()
    generation = _generation(snapshot)
    service, _, _ = _service(snapshot, generation)
    all_registered = tuple(
        replace(item, already_registered=True) for item in generation.all_candidates
    )
    generation = replace(
        generation,
        all_candidates=all_registered,
        candidates=(),
        returned_candidate_count=0,
        already_registered_count=42,
        novel_candidate_count=0,
    )
    service.generator_service.generate.return_value = generation
    result = service.evaluate(reference_snapshot_id=snapshot.id)

    document = result_document(result)
    assert document["generator_step"] == "0.05"
    assert document["historical_evidence_as_of"] == NOW.isoformat()
    assert document["research_profile"]["assumptions"]["fee_rate"] == "0.0005"
    destination = tmp_path / "batch.json"
    write_result_file(destination, document, force=False)
    assert destination.exists()
    with pytest.raises(FileExistsError):
        write_result_file(destination, document, force=False)
    write_result_file(destination, document, force=True)
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_empty_cost_cohorts_preserve_insufficient_status_per_horizon():
    snapshot = _snapshot()
    generation = _generation(snapshot)
    service, _, services = _service(snapshot, generation)
    service._load_and_validate_snapshots = MagicMock(
        return_value=((snapshot,), (snapshot,))
    )
    pipeline = list(_pipeline_results(generation, (snapshot.id,)))
    for index in (5, 6, 7):
        pipeline[index] = SimpleNamespace(
            scenarios=pipeline[index].scenarios,
            horizon_count=3,
            status=pipeline[index].status,
            safe_reason=pipeline[index].safe_reason,
            cohorts=(),
        )
    services[0].evaluate_matrix_snapshots.return_value = pipeline[0]
    services[0].result_from_matrix.return_value = pipeline[1]
    services[1].evaluate_from_matrix.return_value = pipeline[2]
    services[2].evaluate_from_results.return_value = pipeline[3]
    services[3].evaluate_snapshots.return_value = pipeline[4]
    services[4].evaluate_from_results.return_value = pipeline[5]
    services[5].evaluate_from_result.return_value = pipeline[6]
    services[6].evaluate_from_results.return_value = pipeline[7]

    result = service.evaluate(reference_snapshot_id=snapshot.id)

    assert result.status == SUCCESS
    assert all(
        evidence.cost_adjusted_status == "NO_COST_ADJUSTABLE_SNAPSHOTS"
        and evidence.cost_adjusted is None
        for candidate in result.candidate_results
        for evidence in candidate.horizons
    )
