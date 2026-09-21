from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ALREADY_APPROVED,
    CREATED,
    DRY_RUN,
    INVALID_PROMOTION_APPROVAL,
    REVIEW_DECISION_CHANGED,
    REVIEW_NOT_ELIGIBLE,
)
from scripts import approve_shadow_policy_promotion as cli


SIGNATURE = "shadow-review-gate-v1:" + ("a" * 64)


def test_cli_preview_and_apply_arguments() -> None:
    preview = cli.parse_arguments(["--candidate-id", "3"])
    assert preview.apply is False
    apply = cli.parse_arguments(
        [
            "--candidate-id",
            "3",
            "--apply",
            "--expected-review-decision-signature",
            SIGNATURE,
        ]
    )
    assert apply.apply is True


@pytest.mark.parametrize(
    "args",
    (
        ["--candidate-id", "3", "--apply"],
        ["--candidate-id", "3", "--expected-review-decision-signature", SIGNATURE],
    ),
)
def test_cli_enforces_two_step_signature_contract(args) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(args)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "flag",
    (
        "--force",
        "--skip-review",
        "--as-of",
        "--ceiling",
        "--horizon",
        "--fee-rate",
        "--weights",
        "--top-n",
        "--live",
        "--activate",
    ),
)
def test_cli_forbids_review_and_runtime_overrides(flag) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--candidate-id", "3", flag, "1"])
    assert error.value.code == 2


@pytest.mark.parametrize(
    ("status", "commits", "exit_code"),
    (
        (DRY_RUN, 0, 0),
        (CREATED, 1, 0),
        (ALREADY_APPROVED, 0, 0),
        (REVIEW_NOT_ELIGIBLE, 0, 0),
        (REVIEW_DECISION_CHANGED, 0, 0),
        (INVALID_PROMOTION_APPROVAL, 0, 1),
    ),
)
def test_cli_commits_only_created(monkeypatch, status, commits, exit_code) -> None:
    result = SimpleNamespace(approval_status=status)
    monkeypatch.setattr(
        "scripts.approve_shadow_policy_promotion.ShadowPolicyPromotionApprovalService.preview",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr(
        "scripts.approve_shadow_policy_promotion.report",
        lambda value: [value.approval_status],
    )
    session = SimpleNamespace(commit=MagicMock(), rollback=MagicMock())
    lines, actual = cli.run(
        session,
        SimpleNamespace(
            candidate_id=3,
            apply=False,
            expected_review_decision_signature=None,
        ),
    )
    assert lines == [status]
    assert actual == exit_code
    assert session.commit.call_count == commits
    assert session.rollback.call_count == (0 if commits else 1)
