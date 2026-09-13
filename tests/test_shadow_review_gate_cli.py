from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_REVIEW_DATA,
    NOT_ELIGIBLE,
    NO_SHADOW_ENROLLMENT,
)
from scripts import evaluate_shadow_review_gate as cli


def test_cli_accepts_only_candidate_identity() -> None:
    assert cli.parse_arguments(["--candidate-id", "3"]).candidate_id == 3


@pytest.mark.parametrize(
    "flag",
    (
        "--horizon",
        "--fee-rate",
        "--spread-cost-rate",
        "--slippage-rate",
        "--min-days",
        "--min-count",
        "--min-win-rate",
        "--as-of",
        "--ceiling",
        "--apply",
        "--force",
    ),
)
def test_cli_forbids_policy_evidence_and_mutation_overrides(flag) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--candidate-id", "3", flag, "1"])
    assert error.value.code == 2


@pytest.mark.parametrize(
    ("status", "exit_code"),
    (
        (NO_SHADOW_ENROLLMENT, 0),
        (INSUFFICIENT_DATA, 0),
        (NOT_ELIGIBLE, 0),
        (ELIGIBLE_FOR_PROMOTION_REVIEW, 0),
        (INVALID_REVIEW_DATA, 1),
    ),
)
def test_cli_exit_codes_and_rollback(monkeypatch, status, exit_code) -> None:
    result = SimpleNamespace(status=status)
    monkeypatch.setattr(
        "scripts.evaluate_shadow_review_gate.ShadowReviewGateService.evaluate",
        lambda *args, **kwargs: result,
    )
    monkeypatch.setattr(
        "scripts.evaluate_shadow_review_gate.report", lambda value: [value.status]
    )
    session = SimpleNamespace(rollback=MagicMock())
    lines, actual = cli.run(session, SimpleNamespace(candidate_id=3))
    assert lines == [status]
    assert actual == exit_code
    session.rollback.assert_called_once_with()
