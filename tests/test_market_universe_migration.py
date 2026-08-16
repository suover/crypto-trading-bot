from pathlib import Path

from crypto_trading_bot.db.models import (
    AnalysisRun,
    MarketUniverseCandidate,
    TradeRecommendation,
)


def test_market_universe_model_and_migration_are_aligned() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "8e4f1b2c9d6a_add_dynamic_market_universe.py"
    ).read_text(encoding="utf-8")

    assert "pipeline_run_id" in AnalysisRun.__table__.columns
    assert "universe_candidate_id" in TradeRecommendation.__table__.columns
    assert MarketUniverseCandidate.__tablename__ == "market_universe_candidates"
    assert 'down_revision: str | Sequence[str] | None = "f7a1c2d3e4b5"' in migration
    assert '"market_universe_candidates"' in migration
