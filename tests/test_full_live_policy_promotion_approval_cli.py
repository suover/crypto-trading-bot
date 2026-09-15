from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.full_live_policy_promotion_approval_service import (
    CONFIRMATION_MISMATCH,
    CREATED,
    DRY_RUN,
    INTERACTIVE_CONFIRMATION_REQUIRED,
    INVALID_FULL_LIVE_PROMOTION_APPROVAL,
)
from scripts import approve_full_live_policy_promotion as cli


SIGNATURE = "live-canary-review-gate-v1:" + ("a" * 64)


def _result(status, *, artifact=None):
    return SimpleNamespace(
        canary_activation_id=7,
        candidate_id=11,
        approval=None,
        artifact=artifact,
        review=None,
        approval_status=status,
        safe_reason=None,
        review_status="ELIGIBLE_FOR_FULL_LIVE_REVIEW",
        evidence_signature="live-canary-evidence-v1:" + ("b" * 64),
        review_policy_signature="live-canary-review-gate-v1:" + ("c" * 64),
        review_decision_signature=SIGNATURE,
        confirmation_required=status in {DRY_RUN, INTERACTIVE_CONFIRMATION_REQUIRED},
        confirmation_matched=status == CREATED,
        human_approval_recorded=status == CREATED,
        full_live_promotion_approval_created=status == CREATED,
        full_live_promotion_approval_persisted=status == CREATED,
        full_live_policy_activated=False,
        database_write=status == CREATED,
        external_calls=False,
        live_policy_change=False,
        live_order_change=False,
        ranking_runtime_changed=False,
        canary_state_changed=False,
    )


def test_cli_accepts_only_activation_and_apply_arguments():
    preview = cli.parse_arguments(["--canary-activation-id", "7"])
    assert preview.apply is False
    apply = cli.parse_arguments(["--canary-activation-id", "7", "--apply"])
    assert apply.apply is True


@pytest.mark.parametrize(
    "flag",
    (
        "--expected-review-decision-signature",
        "--force",
        "--yes",
        "--activate",
        "--live",
        "--policy",
    ),
)
def test_cli_forbids_signature_reuse_and_runtime_overrides(flag):
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--canary-activation-id", "7", flag, SIGNATURE])
    assert error.value.code == 2


def test_preview_is_informational_and_does_not_prompt(monkeypatch):
    workflow = MagicMock()
    workflow.execute.return_value = _result(DRY_RUN)
    monkeypatch.setattr(
        cli, "FullLivePolicyPromotionApprovalWorkflow", lambda _factory: workflow
    )
    input_fn = MagicMock()
    lines, exit_code = cli.run(
        MagicMock(),
        SimpleNamespace(canary_activation_id=7, apply=False),
        interactive=False,
        input_fn=input_fn,
    )
    assert exit_code == 0
    assert "preview_signature_reusable_for_apply=false" in lines
    assert any("informational only" in line for line in lines)
    assert any("must NOT be reused" in line for line in lines)
    input_fn.assert_not_called()
    workflow.execute.assert_called_once_with(
        canary_activation_id=7,
        apply=False,
        interactive=False,
        confirmation_fn=None,
    )


def test_apply_passes_same_process_exact_signature_prompt(monkeypatch):
    artifact = SimpleNamespace(
        review_checks=({"status": "PASS"},),
        review_status="ELIGIBLE_FOR_FULL_LIVE_REVIEW",
        evidence_signature="live-canary-evidence-v1:" + ("b" * 64),
        review_policy_signature="live-canary-review-gate-v1:" + ("c" * 64),
        review_decision_signature=SIGNATURE,
    )
    workflow = MagicMock()

    def execute(**kwargs):
        assert kwargs["confirmation_fn"](artifact) == SIGNATURE
        return _result(CREATED, artifact=artifact)

    workflow.execute.side_effect = execute
    monkeypatch.setattr(
        cli, "FullLivePolicyPromotionApprovalWorkflow", lambda _factory: workflow
    )
    output = MagicMock()
    lines, exit_code = cli.run(
        MagicMock(),
        SimpleNamespace(canary_activation_id=7, apply=True),
        interactive=True,
        input_fn=lambda _prompt: SIGNATURE,
        output_fn=output,
    )
    assert exit_code == 0
    assert "approval_status=CREATED" in lines
    assert "full_live_policy_activated=false" in lines
    printed = [call.args[0] for call in output.call_args_list]
    assert f"review_decision_signature={SIGNATURE}" in printed
    assert any("enter the exact" in line for line in printed)


@pytest.mark.parametrize(
    ("status", "exit_code"),
    (
        (CONFIRMATION_MISMATCH, 0),
        (INTERACTIVE_CONFIRMATION_REQUIRED, 0),
        (INVALID_FULL_LIVE_PROMOTION_APPROVAL, 1),
    ),
)
def test_cli_exit_code_is_nonzero_only_for_invalid_artifact(
    monkeypatch, status, exit_code
):
    workflow = MagicMock()
    workflow.execute.return_value = _result(status)
    monkeypatch.setattr(
        cli, "FullLivePolicyPromotionApprovalWorkflow", lambda _factory: workflow
    )
    _, actual = cli.run(
        MagicMock(),
        SimpleNamespace(canary_activation_id=7, apply=True),
        interactive=False,
    )
    assert actual == exit_code
