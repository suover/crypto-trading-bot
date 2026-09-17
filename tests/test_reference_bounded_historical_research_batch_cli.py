from decimal import Decimal
import json

import pytest

from scripts.evaluate_generated_candidate_historical_batch import (
    parse_arguments,
    write_result_file,
)


def test_cli_requires_reference_and_uses_fixed_profile_inputs_only():
    parsed = parse_arguments(
        [
            "--reference-snapshot-id",
            "123",
            "--step",
            "0.05",
            "--output",
            "batch.json",
        ]
    )
    assert parsed.reference_snapshot_id == 123
    assert parsed.step == Decimal("0.05")
    assert not hasattr(parsed, "max_candidates")
    for forbidden in (
        "--max-candidates",
        "--horizon",
        "--initial-research-size",
        "--fee-rate",
    ):
        with pytest.raises(SystemExit):
            parse_arguments(["--reference-snapshot-id", "123", forbidden, "1"])


def test_cli_output_is_atomic_and_requires_force_to_replace(tmp_path):
    destination = tmp_path / "batch.json"
    document = {"generator_step": "0.05", "candidate_results": []}

    assert write_result_file(destination, document, force=False) == destination
    assert json.loads(destination.read_text(encoding="utf-8")) == document
    with pytest.raises(FileExistsError):
        write_result_file(destination, {"changed": True}, force=False)
    write_result_file(destination, {"changed": True}, force=True)

    assert json.loads(destination.read_text(encoding="utf-8")) == {"changed": True}
    assert not tuple(tmp_path.glob(".*.tmp"))
