from sqlalchemy import Numeric, UniqueConstraint

from crypto_trading_bot.db.models import (
    PortfolioPositionSnapshot,
    PortfolioSnapshot,
)
from migrations.versions import (
    c3a8d1e5f7b9_add_portfolio_valuation_snapshots as migration,
)


def test_portfolio_migration_extends_current_execution_ledger_head() -> None:
    assert migration.revision == "c3a8d1e5f7b9"
    assert migration.down_revision == "9b7d4e6f2a10"


def test_portfolio_model_financial_precision_and_unique_constraints() -> None:
    for model, columns in (
        (
            PortfolioSnapshot,
            (
                "cash_available_krw",
                "cash_locked_krw",
                "cash_total_krw",
                "priced_positions_value_krw",
                "known_total_value_krw",
                "total_value_krw",
                "positions_estimated_cost_basis_krw",
                "unrealized_pnl_krw",
                "unrealized_pnl_percentage",
            ),
        ),
        (
            PortfolioPositionSnapshot,
            (
                "available_quantity",
                "locked_quantity",
                "total_quantity",
                "avg_buy_price",
                "mark_price",
                "market_value_krw",
                "estimated_cost_basis_krw",
                "unrealized_pnl_krw",
                "unrealized_pnl_percentage",
            ),
        ),
    ):
        for column_name in columns:
            column_type = model.__table__.columns[column_name].type
            assert isinstance(column_type, Numeric)
            assert (column_type.precision, column_type.scale) == (30, 10)

    portfolio_unique_names = {
        constraint.name
        for constraint in PortfolioSnapshot.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    position_unique_names = {
        constraint.name
        for constraint in PortfolioPositionSnapshot.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_portfolio_snapshots_user_exchange_pipeline" in portfolio_unique_names
    assert "uq_portfolio_positions_snapshot_currency" in position_unique_names
