from sqlalchemy import Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from crypto_trading_bot.db.models import AccountActivity, AccountActivitySyncState
from migrations.versions import d4f8a2c6e1b3_add_account_activity_ledger as migration


def test_account_activity_migration_extends_current_head() -> None:
    assert migration.revision == "d4f8a2c6e1b3"
    assert migration.down_revision == "a7c4e9d2f6b1"


def test_account_activity_model_precision_json_and_unique_constraints() -> None:
    for name in (
        "amount",
        "quantity",
        "executed_quantity",
        "executed_funds_krw",
        "paid_fee",
    ):
        column_type = AccountActivity.__table__.columns[name].type
        assert isinstance(column_type, Numeric)
        assert (column_type.precision, column_type.scale) == (30, 10)
    assert isinstance(AccountActivity.__table__.columns.source_metadata.type, JSONB)
    activity_unique = {
        constraint.name
        for constraint in AccountActivity.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    state_unique = {
        constraint.name
        for constraint in AccountActivitySyncState.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_account_activities_owner_source_activity" in activity_unique
    assert "uq_account_activity_sync_states_owner_source" in state_unique
