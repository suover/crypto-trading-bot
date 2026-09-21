from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import evaluate_ranking_holdout_validation as cli
from crypto_trading_bot.services.ranking_holdout_validation_service import (
    INSUFFICIENT_TEMPORAL_SPLIT_DATA,
    INVALID_HOLDOUT_DATA,
)


def aggregate(*, count=2, delta="1"):
    value = Decimal(delta) if delta is not None else None
    return SimpleNamespace(
        snapshot_count=count,
        scenario_win_count=count if value is not None and value > 0 else 0,
        scenario_loss_count=count if value is not None and value < 0 else 0,
        tie_count=count if value == 0 else 0,
        scenario_win_rate=Decimal("1") if value is not None and value > 0 else None,
        mean_baseline_return=Decimal("2") if value is not None else None,
        mean_scenario_return=(Decimal("2") + value) if value is not None else None,
        mean_return_delta=value,
        median_snapshot_return_delta=value,
        mean_baseline_positive_rate=Decimal("0.5") if value is not None else None,
        mean_scenario_positive_rate=Decimal("0.75") if value is not None else None,
    )


def validation_result(*, status="SUCCESS"):
    compared = status == "SUCCESS"
    empty = aggregate(count=0, delta=None)
    research = aggregate() if compared else empty
    holdout = aggregate(count=1, delta="-1") if compared else empty
    scenario = SimpleNamespace(
        name="research_example",
        definition_signature="ranking-scenario-definition-v1:abc",
        component_weights={"liquidity": Decimal("0.2")},
    )
    return SimpleNamespace(
        requested_snapshot_count=10,
        evaluated_snapshot_count=8,
        scenario_count=1,
        horizon_count=1,
        cohort_count=1,
        split_mode="TEMPORAL_RATIO",
        holdout_ratio=Decimal("0.30"),
        research_cutoff_at=None,
        strict_unseen_holdout="false",
        policy_decision_performed=False,
        scenarios=(scenario,),
        cohorts=(
            SimpleNamespace(
                horizon_minutes=240,
                baseline_policy_signature="baseline-a",
                effective_top_n=5,
                candidate_snapshot_count=8,
                common_comparable_snapshot_count=3,
                common_coverage_rate=Decimal("0.375"),
                split_mode="TEMPORAL_RATIO",
                research_snapshot_count=2 if compared else 0,
                holdout_snapshot_count=1 if compared else 0,
                research_start_at=datetime(2026, 1, 1, tzinfo=UTC),
                research_end_at=datetime(2026, 1, 2, tzinfo=UTC),
                holdout_start_at=datetime(2026, 1, 3, tzinfo=UTC),
                holdout_end_at=datetime(2026, 1, 3, tzinfo=UTC),
                status=status,
                safe_reason=None if compared else "not comparable",
                performance_compared=compared,
                scenario_results=(
                    SimpleNamespace(
                        scenario_name=scenario.name,
                        scenario_definition_signature=scenario.definition_signature,
                        research=research,
                        holdout=holdout,
                    ),
                ),
            ),
        ),
    )


def test_help_and_default_latest_ratio_arguments() -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(["--help"])
    assert error.value.code == 0
    parsed = cli.parse_arguments(
        ["--horizon", "60", "--scenario-file", "scenarios.json"]
    )
    assert parsed.latest == 1
    assert parsed.holdout_ratio is None
    assert parsed.research_cutoff_at is None


def test_explicit_ratio_and_fixed_timezone_cutoff_parse() -> None:
    ratio = cli.parse_arguments(
        [
            "--latest",
            "100",
            "--horizon",
            "60",
            "--horizon",
            "240",
            "--scenario-file",
            "scenarios.json",
            "--holdout-ratio",
            "0.25",
        ]
    )
    assert ratio.holdout_ratio == Decimal("0.25")
    assert ratio.horizon == [60, 240]
    cutoff = cli.parse_arguments(
        [
            "--latest",
            "10",
            "--horizon",
            "240",
            "--scenario-file",
            "scenarios.json",
            "--research-cutoff-at",
            "2026-09-01T00:00:00+09:00",
        ]
    )
    assert cutoff.research_cutoff_at.utcoffset().total_seconds() == 9 * 60 * 60


@pytest.mark.parametrize(
    "args",
    [
        ["--scenario-file", "x.json"],
        ["--horizon", "0", "--scenario-file", "x.json"],
        ["--horizon", "60", "--horizon", "60", "--scenario-file", "x.json"],
        ["--latest", "0", "--horizon", "60", "--scenario-file", "x.json"],
        [
            "--horizon",
            "60",
            "--scenario-file",
            "x.json",
            "--holdout-ratio",
            "0.3",
            "--research-cutoff-at",
            "2026-01-01T00:00:00Z",
        ],
        ["--horizon", "60", "--scenario-file", "x.json", "--holdout-ratio", "0"],
        ["--horizon", "60", "--scenario-file", "x.json", "--holdout-ratio", "1"],
        [
            "--horizon",
            "60",
            "--scenario-file",
            "x.json",
            "--holdout-ratio",
            "NaN",
        ],
        [
            "--horizon",
            "60",
            "--scenario-file",
            "x.json",
            "--research-cutoff-at",
            "2026-01-01T00:00:00",
        ],
        [
            "--horizon",
            "60",
            "--scenario-file",
            "x.json",
            "--research-cutoff-at",
            "not-a-date",
        ],
    ],
)
def test_invalid_cli_input_exits_two(args) -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_arguments(args)
    assert error.value.code == 2


def test_run_forwards_explicit_file_and_split_configuration(monkeypatch) -> None:
    namespace = cli.parse_arguments(
        [
            "--latest",
            "10",
            "--horizon",
            "240",
            "--scenario-file",
            "explicit.json",
            "--holdout-ratio",
            "0.25",
        ]
    )
    definition = SimpleNamespace(name="scenario")
    loader = MagicMock(return_value=(definition,))
    service = MagicMock()
    service.evaluate.return_value = validation_result()
    monkeypatch.setattr(cli, "load_scenario_file", loader)
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_holdout_validation_service.RankingHoldoutValidationService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    loader.assert_called_once_with("explicit.json")
    service.evaluate.assert_called_once_with(
        scenarios=(definition,),
        horizons=[240],
        latest=10,
        holdout_ratio=Decimal("0.25"),
        research_cutoff_at=None,
    )
    assert exit_code == 0
    assert "split_mode=TEMPORAL_RATIO" in lines


def test_report_has_safety_headers_metrics_and_no_automatic_decision_fields() -> None:
    lines = cli.report(validation_result())
    assert lines[:8] == [
        "report_type=RANKING_HOLDOUT_VALIDATION",
        "result_type=TEMPORAL_RANKING_HOLDOUT_RESEARCH",
        "performance_metric_type=RANKING_SELECTION_GROSS_MARKET_PERFORMANCE",
        "research_only=true",
        "automatic_policy_selection=false",
        "database_write=false",
        "external_calls=false",
        "live_policy_change=false",
    ]
    assert "research_mean_return_delta=1" in lines
    assert "holdout_mean_return_delta=-1" in lines
    lowered = "\n".join(lines).lower()
    for forbidden in (
        "best_scenario=",
        "winner=",
        "recommended_scenario=",
        "promotion_pass=",
        "apply_to_live=",
    ):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [(INSUFFICIENT_TEMPORAL_SPLIT_DATA, 0), (INVALID_HOLDOUT_DATA, 1)],
)
def test_non_comparable_and_integrity_exit_codes(
    monkeypatch, status, expected_exit
) -> None:
    namespace = cli.parse_arguments(
        ["--horizon", "60", "--scenario-file", "explicit.json"]
    )
    service = MagicMock()
    service.evaluate.return_value = validation_result(status=status)
    monkeypatch.setattr(cli, "load_scenario_file", lambda _: (object(),))
    monkeypatch.setattr(
        "crypto_trading_bot.services.ranking_holdout_validation_service.RankingHoldoutValidationService",
        lambda _: service,
    )
    lines, exit_code = cli.run(MagicMock(), namespace)
    assert exit_code == expected_exit
    assert f"status={status}" in lines
    assert "performance_compared=false" in lines
