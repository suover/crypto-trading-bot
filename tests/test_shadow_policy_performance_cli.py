from types import SimpleNamespace

import pytest

from crypto_trading_bot.services.shadow_policy_performance_service import (
    INVALID_SHADOW_PERFORMANCE_EVIDENCE,
    NO_SHADOW_ENROLLMENT,
    NO_SHADOW_EVALUATIONS,
)
from scripts import evaluate_shadow_policy_performance as cli


def test_cli_requires_identity_horizons_and_explicit_costs() -> None:
    args = cli.parse_arguments(
        [
            "--candidate-id",
            "3",
            "--horizon",
            "60",
            "--horizon",
            "240",
            "--fee-rate",
            "0.0005",
            "--spread-cost-rate",
            "0.0005",
            "--slippage-rate",
            "0.001",
        ]
    )
    assert args.candidate_id == 3
    assert args.horizon == [60, 240]


@pytest.mark.parametrize(
    "flag",
    [
        "--apply",
        "--as-of",
        "--snapshot-id",
        "--latest",
        "--ceiling",
        "--weights",
        "--scenario",
        "--top-n",
        "--skip-enrollment",
        "--force",
    ],
)
def test_cli_forbids_evidence_universe_overrides(flag) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(
            [
                "--candidate-id",
                "3",
                "--horizon",
                "60",
                "--fee-rate",
                "0",
                "--spread-cost-rate",
                "0",
                "--slippage-rate",
                "0",
                flag,
            ]
        )
    assert error.value.code == 2


def test_cli_rejects_duplicate_horizon() -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(
            [
                "--candidate-id",
                "3",
                "--horizon",
                "60",
                "--horizon",
                "60",
                "--fee-rate",
                "0",
                "--spread-cost-rate",
                "0",
                "--slippage-rate",
                "0",
            ]
        )
    assert error.value.code == 2


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (NO_SHADOW_ENROLLMENT, 0),
        (NO_SHADOW_EVALUATIONS, 0),
        (INVALID_SHADOW_PERFORMANCE_EVIDENCE, 1),
    ],
)
def test_cli_safe_and_invalid_exit_codes(monkeypatch, status, exit_code) -> None:
    fake = SimpleNamespace(status=status)
    monkeypatch.setattr(
        "scripts.evaluate_shadow_policy_performance.ShadowPolicyPerformanceService.evaluate",
        lambda *args, **kwargs: fake,
    )
    monkeypatch.setattr(
        "scripts.evaluate_shadow_policy_performance.report",
        lambda result: [result.status],
    )
    session = SimpleNamespace(rollback=lambda: None)
    namespace = SimpleNamespace(
        candidate_id=3,
        horizon=[60],
        fee_rate="0",
        spread_cost_rate="0",
        slippage_rate="0",
    )
    lines, actual = cli.run(session, namespace)
    assert lines == [status]
    assert actual == exit_code
