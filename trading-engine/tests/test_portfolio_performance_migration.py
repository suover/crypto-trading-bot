from decimal import Decimal

from sqlalchemy import Numeric

from crypto_trading_bot.db.models import (
    AccountCashFlowValuation,
    PortfolioPerformanceSnapshot,
)
from migrations.versions import (
    f1b7c3d9e5a2_add_portfolio_performance_foundation as migration,
)


def test_portfolio_performance_migration_extends_account_activity_head() -> None:
    assert migration.revision == "f1b7c3d9e5a2"
    assert migration.down_revision == "d4f8a2c6e1b3"


def test_portfolio_performance_models_use_numeric_and_unique_sources() -> None:
    cash_table = AccountCashFlowValuation.__table__
    performance_table = PortfolioPerformanceSnapshot.__table__
    assert cash_table.c.account_activity_id.unique is True
    assert cash_table.c.native_amount.nullable is True
    assert cash_table.c.event_time.nullable is True
    assert cash_table.c.direction.nullable is True
    assert cash_table.c.currency.nullable is True
    assert performance_table.c.portfolio_snapshot_id.unique is True
    for column in (
        cash_table.c.native_amount,
        cash_table.c.valuation_price_krw,
        cash_table.c.cash_flow_value_krw,
        performance_table.c.performance_index,
        performance_table.c.high_water_mark_krw,
        performance_table.c.drawdown_krw,
        performance_table.c.period_return_percentage,
        performance_table.c.max_drawdown_percentage,
    ):
        assert isinstance(column.type, Numeric)
        assert column.type.precision == 30
        assert column.type.scale == 10
    assert isinstance(Decimal("1.0"), Decimal)
