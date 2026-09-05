from pathlib import Path

from crypto_trading_bot.db.models import (
    StrategyReplayCandidate,
    StrategyReplaySnapshot,
)


def test_strategy_replay_models_and_migration_are_aligned() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "d1e3f5a7b9c2_add_strategy_replay_dataset.py"
    ).read_text(encoding="utf-8")
    assert StrategyReplaySnapshot.__tablename__ == "strategy_replay_snapshots"
    assert StrategyReplayCandidate.__tablename__ == "strategy_replay_candidates"
    assert 'down_revision: str | Sequence[str] | None = "c9d2e4f6a8b1"' in migration
    assert "uq_strategy_replay_snapshots_analysis_run" in migration
    assert "uq_strategy_replay_candidates_snapshot_exchange_market" in migration
    assert "postgresql.JSONB" in migration
    assert 'ondelete="CASCADE"' in migration
