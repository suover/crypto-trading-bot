from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.live_policy_canary_service import CREATED, DRY_RUN
from scripts import check_live_policy_canary as status_cli
from scripts import start_live_policy_canary as start_cli


SIGNATURE = "human-approved-promotion-v1:" + ("a" * 64)


def test_start_cli_enforces_two_step_contract():
    assert start_cli.parse_arguments(["--promotion-approval-id", "1"]).apply is False
    assert (
        start_cli.parse_arguments(
            [
                "--promotion-approval-id",
                "1",
                "--expected-approval-signature",
                SIGNATURE,
                "--apply",
            ]
        ).apply
        is True
    )
    with pytest.raises(SystemExit):
        start_cli.parse_arguments(["--promotion-approval-id", "1", "--apply"])


@pytest.mark.parametrize(
    "flag",
    (
        "--candidate-id",
        "--weights",
        "--duration",
        "--max-runs",
        "--force",
        "--order-cap",
        "--daily-cap",
    ),
)
def test_start_cli_rejects_runtime_overrides(flag):
    with pytest.raises(SystemExit):
        start_cli.parse_arguments(["--promotion-approval-id", "1", flag, "1"])


@pytest.mark.parametrize(("status", "rollbacks"), ((DRY_RUN, 1), (CREATED, 0)))
def test_start_cli_only_service_created_path_keeps_commit(
    status, rollbacks, monkeypatch
):
    result = SimpleNamespace(activation_status=status)
    method = "activate" if status == CREATED else "preview"
    monkeypatch.setattr(
        f"scripts.start_live_policy_canary.LivePolicyCanaryActivationService.{method}",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr("scripts.start_live_policy_canary.report", lambda _: [status])
    session = SimpleNamespace(rollback=MagicMock())
    namespace = SimpleNamespace(
        promotion_approval_id=1,
        apply=status == CREATED,
        expected_approval_signature=SIGNATURE if status == CREATED else None,
    )
    lines, code = start_cli.run(session, namespace)
    assert lines == [status]
    assert code == 0
    assert session.rollback.call_count == rollbacks


def test_status_cli_is_read_only(monkeypatch):
    result = SimpleNamespace(mode="BASELINE_NO_CANARY")
    monkeypatch.setattr(
        "scripts.check_live_policy_canary.LiveRankingPolicyResolver.inspect",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr("scripts.check_live_policy_canary.report", lambda _: ["ok"])
    session = SimpleNamespace(rollback=MagicMock())
    assert status_cli.run(session, SimpleNamespace(user_name="Minsu")) == ["ok"]
    session.rollback.assert_called_once()
