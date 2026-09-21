from pathlib import Path

from crypto_trading_bot.db.models import StrategyReplayCandidateOutcome


def test_research_candidate_outcome_model_and_migration_are_aligned() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "e2f4a6b8c0d1_add_research_candidate_outcomes.py"
    ).read_text(encoding="utf-8")
    assert StrategyReplayCandidateOutcome.__tablename__ == (
        "strategy_replay_candidate_outcomes"
    )
    assert 'down_revision: str | Sequence[str] | None = "d1e3f5a7b9c2"' in migration
    assert "uq_strategy_replay_candidate_outcomes_candidate_horizon" in migration
    for index in (
        "ix_srco_candidate_id",
        "ix_srco_snapshot_id",
        "ix_srco_user_id",
        "ix_srco_user_target",
        "ix_srco_status_target",
    ):
        assert index in migration
    assert migration.count('ondelete="CASCADE"') == 2
    assert "sa.Numeric(30, 10)" in migration
    assert "sa.Numeric(30, 12)" in migration
    assert "sa.DateTime(timezone=True)" in migration
