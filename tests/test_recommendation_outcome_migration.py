from pathlib import Path

from crypto_trading_bot.db.models import (
    TradeRecommendationCandidateOutcome,
    TradeRecommendationOutcome,
)


def test_recommendation_outcome_models_and_migration_are_aligned() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "c9d2e4f6a8b1_add_recommendation_outcomes.py"
    ).read_text(encoding="utf-8")
    assert TradeRecommendationOutcome.__tablename__ == "trade_recommendation_outcomes"
    assert (
        TradeRecommendationCandidateOutcome.__tablename__
        == "trade_recommendation_candidate_outcomes"
    )
    assert 'down_revision: str | Sequence[str] | None = "b8c2d4e6f1a3"' in migration
    assert "uq_recommendation_outcomes_recommendation_horizon" in migration
    assert "uq_candidate_outcomes_recommendation_candidate_horizon" in migration
    assert "sa.Numeric(30, 12)" in migration
    assert "sa.DateTime(timezone=True)" in migration
