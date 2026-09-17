from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts.evaluate_historical_candidate_screening_gate import (
    parse_arguments,
    report,
    run,
    write_result_file,
)


def test_cli_arguments_and_force_contract():
    namespace = parse_arguments(
        ["--reference-snapshot-id", "39", "--step", "0.05", "--output", "x"]
    )
    assert namespace.reference_snapshot_id == 39
    assert str(namespace.step) == "0.05"
    with pytest.raises(SystemExit):
        parse_arguments(["--reference-snapshot-id", "39", "--force"])


def test_atomic_output_refuses_overwrite_without_force(tmp_path):
    path = tmp_path / "screening.json"
    write_result_file(path, {"status": "SUCCESS"}, force=False)
    assert path.read_text(encoding="utf-8").endswith("\n")
    with pytest.raises(FileExistsError):
        write_result_file(path, {"status": "changed"}, force=False)
    write_result_file(path, {"status": "changed"}, force=True)
    assert '"changed"' in path.read_text(encoding="utf-8")
    assert not tuple(tmp_path.glob(".screening.json.*.tmp"))


def test_run_uses_read_only_service_and_compact_report(tmp_path):
    result = SimpleNamespace(
        report_type="HISTORICAL_CANDIDATE_SCREENING_GATE",
        gate_schema_version="historical-candidate-screening-gate-v1",
        screening_policy_schema_version="historical-candidate-screening-policy-v1",
        screening_policy_signature="signature",
        reference_snapshot_id=39,
        historical_evidence_as_of=None,
        candidate_count=1,
        pass_count=1,
        fail_count=0,
        insufficient_count=0,
        invalid_count=0,
        status="SUCCESS",
        safe_reason=None,
        research_only=True,
        historical_screening_performed=True,
        automatic_policy_selection=False,
        candidate_registration_performed=False,
        database_write=False,
        external_calls=False,
        live_policy_change=False,
        candidate_results=(
            SimpleNamespace(
                scenario_name="candidate",
                scenario_definition_signature="candidate-signature",
                donor_field="liquidity",
                receiver_field="trend",
                status="PASS",
                passed_checks=(1, 2, 3),
                insufficient_checks=(),
                failed_checks=(),
                invalid_checks=(),
            ),
        ),
    )
    namespace = SimpleNamespace(
        reference_snapshot_id=39,
        step="0.05",
        output=None,
        force=False,
    )
    service = MagicMock()
    service.evaluate.return_value = result
    with patch(
        "scripts.evaluate_historical_candidate_screening_gate."
        "HistoricalCandidateScreeningGateService",
        return_value=service,
    ):
        lines, exit_code = run(MagicMock(), namespace)
    assert exit_code == 0
    assert "candidate_status=PASS" in lines
    assert "automatic_policy_selection=false" in lines
    service.evaluate.assert_called_once_with(reference_snapshot_id=39, step="0.05")


def test_report_does_not_dump_raw_batch_evidence():
    result = MagicMock()
    result.report_type = "HISTORICAL_CANDIDATE_SCREENING_GATE"
    result.gate_schema_version = "gate-v1"
    result.screening_policy_schema_version = "policy-v1"
    result.screening_policy_signature = "signature"
    result.reference_snapshot_id = 39
    result.historical_evidence_as_of = None
    result.candidate_count = 0
    result.pass_count = 0
    result.fail_count = 0
    result.insufficient_count = 0
    result.invalid_count = 0
    result.status = "NO_CANDIDATES_TO_SCREEN"
    result.safe_reason = None
    result.research_only = True
    result.historical_screening_performed = False
    result.automatic_policy_selection = False
    result.candidate_registration_performed = False
    result.database_write = False
    result.external_calls = False
    result.live_policy_change = False
    result.candidate_results = ()
    output = "\n".join(report(result, output_path=Path("result.json")))
    assert "gross_matrix" not in output
    assert "candidate_count=0" in output
