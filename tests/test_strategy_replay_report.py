from types import SimpleNamespace
from unittest.mock import MagicMock

from scripts.report_strategy_replay_dataset import build_report


def test_empty_report_is_safe_and_read_only() -> None:
    session = MagicMock()
    session.scalar.side_effect = [0, 0, None]
    lines = build_report(session)
    assert lines == [
        "report_type=STRATEGY_REPLAY_DATASET",
        "snapshot_count=0",
        "research_candidate_count=0",
        "latest_snapshot_id=None",
    ]
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_populated_report_prints_latest_summary_and_candidate_fields() -> None:
    session = MagicMock()
    latest = SimpleNamespace(
        id=10,
        pipeline_run_id="pipeline-10",
        policy_signature="strategy-replay-v1:abc",
        research_candidate_count=4,
        prefilter_candidate_count=3,
        ranked_candidate_count=3,
        final_candidate_count=2,
    )
    session.scalar.side_effect = [2, 7, latest, 3, 2, 1]
    session.scalars.return_value = [
        SimpleNamespace(
            market="KRW-BTC",
            prefilter_rank=1,
            original_rank=1,
            original_score="0.9",
            final_selected=True,
            selection_source="RANKED",
        )
    ]
    lines = build_report(session, limit=1)
    assert "snapshot_count=2" in lines
    assert "research_candidate_count=7" in lines
    assert "latest_prefilter_candidate_count=3" in lines
    assert "in_prefilter_count=3" in lines
    assert "final_selected_count=2" in lines
    assert "held_count=1" in lines
    assert any(
        "market=KRW-BTC prefilter_rank=1 original_rank=1" in line for line in lines
    )
    session.add.assert_not_called()
    session.commit.assert_not_called()
