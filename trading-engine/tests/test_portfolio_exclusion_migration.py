from crypto_trading_bot.db.models import PortfolioSnapshot
from migrations.versions import (
    b8c2d4e6f1a3_add_portfolio_valuation_policy_identity as migration,
)


def test_portfolio_policy_migration_extends_current_head() -> None:
    assert migration.revision == "b8c2d4e6f1a3"
    assert migration.down_revision == "f1b7c3d9e5a2"


def test_portfolio_policy_signature_is_nullable_for_legacy_snapshots() -> None:
    column = PortfolioSnapshot.__table__.c.valuation_policy_signature

    assert column.nullable is True
    assert column.type.length == 100
