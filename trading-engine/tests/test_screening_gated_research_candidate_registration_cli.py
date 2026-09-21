from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts.register_screened_research_candidates import (
    parse_arguments,
    run,
    write_result_file,
)


def test_cli_preview_and_apply_signature_contract():
    preview = parse_arguments(["--reference-snapshot-id", "52"])
    assert not preview.apply
    apply = parse_arguments(
        [
            "--reference-snapshot-id",
            "52",
            "--apply",
            "--expected-plan-signature",
            "screening-gated-research-candidate-registration-v1:" + "a" * 64,
        ]
    )
    assert apply.apply
    with pytest.raises(SystemExit):
        parse_arguments(["--reference-snapshot-id", "52", "--apply"])
    with pytest.raises(SystemExit):
        parse_arguments(
            [
                "--reference-snapshot-id",
                "52",
                "--expected-plan-signature",
                "unexpected",
            ]
        )


def test_output_is_atomic_and_requires_force(tmp_path):
    path = tmp_path / "registration.json"
    write_result_file(path, {"status": "READY"}, force=False)
    with pytest.raises(FileExistsError):
        write_result_file(path, {"status": "CREATED"}, force=False)
    write_result_file(path, {"status": "CREATED"}, force=True)
    assert '"CREATED"' in path.read_text(encoding="utf-8")
    assert not tuple(tmp_path.glob(".registration.json.*.tmp"))


def _result(status):
    return SimpleNamespace(
        report_type="SCREENING_GATED_RESEARCH_CANDIDATE_REGISTRATION",
        schema_version="screening-gated-research-candidate-registration-v1",
        apply_requested=status == "CREATED",
        status=status,
        safe_reason=None,
        registration_plan=None,
        registration_plan_signature="signature",
        screened_candidate_count=2,
        pass_candidate_count=2,
        registration_candidate_count=2,
        created_candidate_count=2 if status == "CREATED" else 0,
        candidate_results=(),
        shared_registered_at=None,
        shared_registration_snapshot_id_watermark=None,
        shared_registration_captured_at_watermark=None,
        candidate_registration_performed=status == "CREATED",
        database_write=status == "CREATED",
    )


@pytest.mark.parametrize(
    "apply,status,commit_count,rollback_count",
    [(False, "READY", 0, 1), (True, "CREATED", 1, 0)],
)
def test_cli_commits_exactly_once_only_for_created(
    apply, status, commit_count, rollback_count
):
    session = MagicMock()
    namespace = SimpleNamespace(
        reference_snapshot_id=52,
        step="0.05",
        apply=apply,
        expected_plan_signature="signature" if apply else None,
        output=None,
        force=False,
    )
    service = MagicMock()
    if apply:
        service.apply.return_value = _result(status)
    else:
        service.preview.return_value = _result(status)
    with patch(
        "scripts.register_screened_research_candidates."
        "ScreeningGatedResearchCandidateRegistrationService",
        return_value=service,
    ):
        _, exit_code = run(session, namespace)
    assert exit_code == 0
    assert session.commit.call_count == commit_count
    assert session.rollback.call_count == rollback_count
