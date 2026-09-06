from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from crypto_trading_bot.services.offline_strategy_replay_service import (
    COMPONENT_WEIGHT_FIELDS,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    ScenarioDefinitionError,
    load_scenario_file,
    parse_scenario_document,
)


WEIGHTS = {
    "liquidity": "0.20",
    "trend_alignment": "0.20",
    "momentum": "0.30",
    "volume_confirmation": "0.10",
    "spread": "0.08",
    "volatility": "0.07",
    "drawdown": "0.05",
}


def document(name="scenario_a", weights=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "scenarios": [
            {"name": name, "component_weights": weights or deepcopy(WEIGHTS)}
        ],
    }


def test_valid_document_uses_current_component_fields_and_decimal_weights() -> None:
    definition = parse_scenario_document(document())[0]
    assert set(definition.component_weights) == set(COMPONENT_WEIGHT_FIELDS)
    assert all(
        isinstance(value, Decimal) for value in definition.component_weights.values()
    )
    assert sum(definition.component_weights.values()) == Decimal("1")
    assert definition.definition_signature.startswith("ranking-scenario-definition-v1:")


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {},
        {"schema_version": "wrong", "scenarios": [{}]},
        {"schema_version": SCHEMA_VERSION},
        {"schema_version": SCHEMA_VERSION, "scenarios": []},
        {"schema_version": SCHEMA_VERSION, "scenarios": "not-an-array"},
        {"schema_version": SCHEMA_VERSION, "scenarios": [], "unknown": True},
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [{"name": "x", "component_weights": WEIGHTS, "extra": 1}],
        },
    ],
)
def test_invalid_root_schema_and_scenario_shape_are_rejected(invalid) -> None:
    with pytest.raises(ScenarioDefinitionError):
        parse_scenario_document(invalid)


@pytest.mark.parametrize(
    "name", ["", " BASELINE", "BASELINE", "baseline", "bad name", "한글"]
)
def test_invalid_or_reserved_names_are_rejected(name) -> None:
    with pytest.raises(ScenarioDefinitionError):
        parse_scenario_document(document(name=name))


def test_duplicate_names_and_duplicate_weight_vectors_reject_entire_file() -> None:
    duplicate_name = document()
    duplicate_name["scenarios"].append(deepcopy(duplicate_name["scenarios"][0]))
    with pytest.raises(ScenarioDefinitionError, match="duplicate scenario name"):
        parse_scenario_document(duplicate_name)

    duplicate_weights = document()
    duplicate_weights["scenarios"].append(
        {"name": "scenario_b", "component_weights": deepcopy(WEIGHTS)}
    )
    with pytest.raises(ScenarioDefinitionError, match="duplicate scenario component"):
        parse_scenario_document(duplicate_weights)


def test_missing_and_unknown_component_fields_are_rejected() -> None:
    missing = deepcopy(WEIGHTS)
    missing.pop("drawdown")
    unknown = deepcopy(WEIGHTS)
    unknown["unexpected"] = unknown.pop("drawdown")
    for weights in (missing, unknown):
        with pytest.raises(ScenarioDefinitionError, match="exact component field set"):
            parse_scenario_document(document(weights=weights))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("liquidity", True),
        ("liquidity", "NaN"),
        ("liquidity", "Infinity"),
        ("liquidity", "-0.1"),
        ("liquidity", "bad"),
    ],
)
def test_invalid_component_values_are_rejected(field, value) -> None:
    weights = deepcopy(WEIGHTS)
    weights[field] = value
    with pytest.raises(ScenarioDefinitionError):
        parse_scenario_document(document(weights=weights))


def test_component_sum_must_equal_one_exactly() -> None:
    weights = deepcopy(WEIGHTS)
    weights["momentum"] = "0.29"
    with pytest.raises(ScenarioDefinitionError, match="must sum to 1"):
        parse_scenario_document(document(weights=weights))


def test_signature_is_deterministic_name_independent_and_weight_sensitive() -> None:
    first = parse_scenario_document(document(name="one"))[0]
    reordered = dict(reversed(list(WEIGHTS.items())))
    renamed = parse_scenario_document(document(name="two", weights=reordered))[0]
    changed_weights = deepcopy(WEIGHTS)
    changed_weights["liquidity"] = "0.21"
    changed_weights["momentum"] = "0.29"
    changed = parse_scenario_document(document(weights=changed_weights))[0]
    assert first.definition_signature == renamed.definition_signature
    assert first.definition_signature != changed.definition_signature


def test_json_numbers_are_accepted_and_immediately_normalized_to_decimal() -> None:
    numbers = {name: float(value) for name, value in WEIGHTS.items()}
    parsed = parse_scenario_document(document(weights=numbers))[0]
    assert parsed.component_weights["momentum"] == Decimal("0.3")


def test_invalid_json_and_missing_file_fail_closed(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    for path in (invalid, tmp_path / "missing.json"):
        with pytest.raises(ScenarioDefinitionError, match="readable JSON"):
            load_scenario_file(path)


def test_example_file_is_valid_and_explicit_only() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "ranking_scenarios.example.json"
    )
    definitions = load_scenario_file(path)
    assert [item.name for item in definitions] == [
        "research_example_momentum_heavy",
        "research_example_liquidity_heavy",
    ]
