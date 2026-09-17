from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import generate_ranking_candidates as cli
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    CandidateGenerationError,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    load_scenario_file,
    parse_scenario_document,
)


WEIGHTS_A = {
    "liquidity": Decimal("0.30"),
    "trend_alignment": Decimal("0.25"),
    "momentum": Decimal("0.15"),
    "volume_confirmation": Decimal("0.10"),
    "spread": Decimal("0.08"),
    "volatility": Decimal("0.07"),
    "drawdown": Decimal("0.05"),
}
WEIGHTS_B = {
    **WEIGHTS_A,
    "liquidity": Decimal("0.35"),
    "trend_alignment": Decimal("0.20"),
}


def _definition(name, weights):
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [{"name": name, "component_weights": weights}],
        }
    )[0]


def _result(*, include_registered=False):
    definitions = (
        _definition("auto_v1_liquidity_to_trend_alignment_p0.05", WEIGHTS_A),
        _definition("auto_v1_trend_alignment_to_liquidity_p0.05", WEIGHTS_B),
    )
    candidates = tuple(
        SimpleNamespace(
            scenario_name=definition.name,
            scenario_definition_signature=definition.definition_signature,
            component_weights=definition.component_weights,
            donor_field=("liquidity" if index == 0 else "trend_alignment"),
            receiver_field=("trend_alignment" if index == 0 else "liquidity"),
            transfer_step=Decimal("0.05"),
            already_registered=index == 1,
        )
        for index, definition in enumerate(definitions)
    )
    return SimpleNamespace(
        generator_schema_version="deterministic-ranking-candidate-generator-v1",
        reference_snapshot_id=123,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        dataset_schema_version="strategy-replay-dataset-v1",
        reference_policy_signature="strategy-replay-v1:abc",
        effective_top_n=7,
        step=Decimal("0.05"),
        max_candidates=20,
        generated_before_dedup_count=2,
        generated_valid_count=2,
        returned_candidate_count=(2 if include_registered else 1),
        duplicate_removed_count=0,
        already_registered_count=1,
        novel_candidate_count=1,
        selection_includes_registered=include_registered,
        candidate_cap_applied=False,
        all_candidates=candidates,
        candidates=(candidates if include_registered else candidates[:1]),
        database_write=False,
        external_calls=False,
        outcome_data_used=False,
        performance_evaluated=False,
        policy_decision_performed=False,
        promotion_performed=False,
        shadow_runtime_changed=False,
        live_policy_change=False,
        live_order_change=False,
    )


def test_help_and_defaults_are_available():
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--help"])
    assert error.value.code == 0
    namespace = cli.parse_arguments(["--reference-snapshot-id", "123"])
    assert namespace.reference_snapshot_id == 123
    assert namespace.step == Decimal("0.05")
    assert namespace.max_candidates == 20
    assert namespace.output is None
    assert namespace.include_registered is False
    assert namespace.force is False


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--reference-snapshot-id", "0"],
        ["--reference-snapshot-id", "1", "--step", "0"],
        ["--reference-snapshot-id", "1", "--step", "NaN"],
        ["--reference-snapshot-id", "1", "--step", "0.11"],
        ["--reference-snapshot-id", "1", "--max-candidates", "0"],
        ["--reference-snapshot-id", "1", "--max-candidates", "51"],
        ["--reference-snapshot-id", "1", "--force"],
    ],
)
def test_invalid_cli_arguments_exit_two(args):
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(args)
    assert error.value.code == 2


def test_scenario_document_defaults_to_novel_and_round_trips_parser():
    document = cli.scenario_document(_result(), include_registered=False)
    definitions = parse_scenario_document(document)
    assert len(definitions) == 1
    assert definitions[0].name.endswith("liquidity_to_trend_alignment_p0.05")
    assert definitions[0].component_weights == WEIGHTS_A
    assert all(
        isinstance(value, str)
        for value in document["scenarios"][0]["component_weights"].values()
    )


def test_scenario_document_can_include_registered_for_diagnostics():
    document = cli.scenario_document(
        _result(include_registered=True), include_registered=True
    )
    assert len(parse_scenario_document(document)) == 2


def test_zero_novel_result_is_normal_until_scenario_output_is_requested():
    result = _result()
    for candidate in result.candidates:
        candidate.already_registered = True
    with pytest.raises(CandidateGenerationError, match="no generated candidates"):
        cli.scenario_document(result, include_registered=False)
    assert len(cli.scenario_document(result, include_registered=True)["scenarios"]) == 2


def test_atomic_output_round_trips_and_requires_force_for_existing_file(tmp_path):
    destination = tmp_path / "generated.json"
    document = cli.scenario_document(_result(), include_registered=False)
    assert cli.write_scenario_file(destination, document, force=False) == destination
    definitions = load_scenario_file(destination)
    assert len(definitions) == 1
    assert not tuple(tmp_path.glob("*.tmp"))
    with pytest.raises(CandidateGenerationError, match="already exists"):
        cli.write_scenario_file(destination, document, force=False)
    cli.write_scenario_file(destination, document, force=True)
    assert len(load_scenario_file(destination)) == 1


def test_run_forwards_include_registered_selection_policy(monkeypatch):
    service = MagicMock()
    service.generate.return_value = _result(include_registered=True)
    monkeypatch.setattr(
        cli, "DeterministicRankingCandidateGeneratorService", lambda _: service
    )
    namespace = cli.parse_arguments(
        ["--reference-snapshot-id", "123", "--include-registered"]
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    service.generate.assert_called_once_with(
        reference_snapshot_id=123,
        step=Decimal("0.05"),
        max_candidates=20,
        include_registered=True,
    )
    assert exit_code == 0
    assert "selection_includes_registered=true" in lines


def test_run_is_read_only_and_writes_only_requested_scenario_file(
    monkeypatch, tmp_path
):
    result = _result()
    service = MagicMock()
    service.generate.return_value = result
    monkeypatch.setattr(
        cli, "DeterministicRankingCandidateGeneratorService", lambda _: service
    )
    destination = tmp_path / "generated.json"
    namespace = cli.parse_arguments(
        [
            "--reference-snapshot-id",
            "123",
            "--step",
            "0.05",
            "--max-candidates",
            "10",
            "--output",
            str(destination),
        ]
    )
    session = MagicMock()
    lines, exit_code = cli.run(session, namespace)
    service.generate.assert_called_once_with(
        reference_snapshot_id=123,
        step=Decimal("0.05"),
        max_candidates=10,
        include_registered=False,
    )
    assert exit_code == 0
    assert "database_write=false" in lines
    assert "external_calls=false" in lines
    assert "outcome_data_used=false" in lines
    assert "performance_evaluated=false" in lines
    assert "policy_decision_performed=false" in lines
    assert "live_policy_change=false" in lines
    assert "output_candidate_count=1" in lines
    assert len(load_scenario_file(destination)) == 1
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
