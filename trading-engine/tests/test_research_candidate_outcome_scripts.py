from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.rebuild_research_candidate_outcomes import parse_arguments as rebuild_args
from scripts.report_research_candidate_outcomes import build_report, parse_arguments


def test_rebuild_defaults_to_dry_run_and_supports_filters() -> None:
    defaults = rebuild_args([])
    assert defaults.apply is False
    assert defaults.snapshot_id is None
    assert defaults.user_id is None
    assert defaults.limit is None
    filtered = rebuild_args(
        ["--apply", "--snapshot-id", "7", "--user-id", "3", "--limit", "20"]
    )
    assert filtered.apply is True
    assert (filtered.snapshot_id, filtered.user_id, filtered.limit) == (7, 3, 20)


@pytest.mark.parametrize(
    "args", [["--snapshot-id", "0"], ["--user-id", "0"], ["--limit", "0"]]
)
def test_rebuild_rejects_invalid_positive_options(args) -> None:
    with pytest.raises(SystemExit) as error:
        rebuild_args(args)
    assert error.value.code == 2


def test_report_empty_database_is_safe_and_read_only() -> None:
    session = MagicMock()
    session.scalars.return_value = []
    session.execute.return_value = []
    lines = build_report(session)
    assert lines[:5] == [
        "report_type=RESEARCH_CANDIDATE_OUTCOME",
        "result_type=GROSS_MARKET_MOVEMENT_NOT_TRADING_PNL",
        "outcome_count=0",
        "complete_count=0",
        "partial_count=0",
    ]
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_report_outputs_horizon_statistics_and_candidate_detail() -> None:
    complete = SimpleNamespace(
        id=1,
        strategy_replay_snapshot_id=5,
        strategy_replay_candidate_id=10,
        user_id=1,
        market="KRW-BTC",
        horizon_minutes=60,
        reference_price=Decimal("100"),
        end_price=Decimal("110"),
        market_return_percentage=Decimal("10"),
        evaluation_status="COMPLETE",
        safe_reason=None,
    )
    partial = SimpleNamespace(
        id=2,
        strategy_replay_snapshot_id=5,
        strategy_replay_candidate_id=11,
        user_id=1,
        market="KRW-ETH",
        horizon_minutes=60,
        reference_price=Decimal("100"),
        end_price=None,
        market_return_percentage=None,
        evaluation_status="PARTIAL",
        safe_reason="HISTORICAL_PRICE_UNAVAILABLE",
    )
    session = MagicMock()
    session.scalars.return_value = [complete, partial]
    session.execute.return_value = [
        (complete, SimpleNamespace(original_rank=1)),
        (partial, SimpleNamespace(original_rank=2)),
    ]
    lines = build_report(session, user_id=1, limit=2)
    assert "outcome_count=2" in lines
    assert any(
        "horizon_minutes=60 total=2 complete=1 partial=1" in line
        and "mean_market_return=10" in line
        and "median_market_return=10" in line
        for line in lines
    )
    assert any("snapshot_id=5 market=KRW-BTC original_rank=1" in line for line in lines)


def test_report_arguments_validate_positive_values() -> None:
    assert parse_arguments(["--user-id", "1", "--limit", "2"]).limit == 2
    for args in (["--user-id", "0"], ["--limit", "0"]):
        with pytest.raises(SystemExit):
            parse_arguments(args)
