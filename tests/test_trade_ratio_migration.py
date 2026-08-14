from pathlib import Path

from crypto_trading_bot.db.models import TradeRecommendation


def test_trade_ratio_model_and_migration_are_aligned() -> None:
    column = TradeRecommendation.__table__.c.trade_ratio
    assert column.nullable is True
    assert column.type.precision == 10
    assert column.type.scale == 9
    migration = Path(
        "migrations/versions/f7a1c2d3e4b5_add_trade_ratio_to_recommendations.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "c5e7a9b1d3f4"' in migration
    assert "op.add_column(" in migration
    assert 'op.drop_column("trade_recommendations", "trade_ratio")' in migration
