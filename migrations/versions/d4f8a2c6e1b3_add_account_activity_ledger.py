"""add account activity ledger

Revision ID: d4f8a2c6e1b3
Revises: a7c4e9d2f6b1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4f8a2c6e1b3"
down_revision: str | Sequence[str] | None = "a7c4e9d2f6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_activities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("activity_type", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=30), nullable=False),
        sa.Column("exchange_activity_id", sa.String(length=100), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=True),
        sa.Column("currency", sa.String(length=20), nullable=True),
        sa.Column("side", sa.String(length=20), nullable=True),
        sa.Column("order_type", sa.String(length=30), nullable=True),
        sa.Column("identifier", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(30, 10), nullable=True),
        sa.Column("quantity", sa.Numeric(30, 10), nullable=True),
        sa.Column("executed_quantity", sa.Numeric(30, 10), nullable=True),
        sa.Column("executed_funds_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("paid_fee", sa.Numeric(30, 10), nullable=True),
        sa.Column("fee_currency", sa.String(length=20), nullable=True),
        sa.Column("cash_flow_direction", sa.String(length=10), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('UPBIT_CLOSED_ORDER', 'UPBIT_DEPOSIT', "
            "'UPBIT_WITHDRAWAL')",
            name="ck_account_activities_source_type",
        ),
        sa.CheckConstraint(
            "activity_type IN ('ORDER', 'DEPOSIT', 'WITHDRAWAL')",
            name="ck_account_activities_activity_type",
        ),
        sa.CheckConstraint(
            "origin IN ('BOT', 'EXTERNAL', 'ACCOUNT_EXTERNAL')",
            name="ck_account_activities_origin",
        ),
        sa.CheckConstraint(
            "cash_flow_direction IS NULL OR cash_flow_direction IN ('IN', 'OUT')",
            name="ck_account_activities_cash_flow_direction",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "exchange",
            "source_type",
            "exchange_activity_id",
            name="uq_account_activities_owner_source_activity",
        ),
    )
    op.create_index(
        "ix_account_activities_owner_occurred",
        "account_activities",
        ["user_id", "exchange", "occurred_at"],
    )
    op.create_index(
        "ix_account_activities_type_occurred",
        "account_activities",
        ["activity_type", "occurred_at"],
    )
    op.create_index(
        "ix_account_activities_origin_type_occurred",
        "account_activities",
        ["origin", "activity_type", "occurred_at"],
    )

    op.create_table(
        "account_activity_sync_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column(
            "sync_status",
            sa.String(length=20),
            server_default=sa.text("'NEVER_SYNCED'"),
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_safe_error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sync_status IN ('NEVER_SYNCED', 'COMPLETE', 'FAILED', 'OUT_OF_SCOPE')",
            name="ck_account_activity_sync_states_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "exchange",
            "source_type",
            name="uq_account_activity_sync_states_owner_source",
        ),
    )
    op.create_index(
        op.f("ix_account_activity_sync_states_user_id"),
        "account_activity_sync_states",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_account_activity_sync_states_user_id"),
        table_name="account_activity_sync_states",
    )
    op.drop_table("account_activity_sync_states")
    op.drop_index(
        "ix_account_activities_origin_type_occurred",
        table_name="account_activities",
    )
    op.drop_index(
        "ix_account_activities_type_occurred", table_name="account_activities"
    )
    op.drop_index(
        "ix_account_activities_owner_occurred", table_name="account_activities"
    )
    op.drop_table("account_activities")
