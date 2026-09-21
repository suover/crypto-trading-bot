"""add portfolio performance foundation

Revision ID: f1b7c3d9e5a2
Revises: d4f8a2c6e1b3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f1b7c3d9e5a2"
down_revision: str | Sequence[str] | None = "d4f8a2c6e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_cash_flow_valuations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_activity_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("direction", sa.String(10), nullable=True),
        sa.Column("currency", sa.String(20), nullable=True),
        sa.Column("native_amount", sa.Numeric(30, 10), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valuation_price_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("cash_flow_value_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("price_source", sa.String(50), nullable=False),
        sa.Column("valuation_status", sa.String(20), nullable=False),
        sa.Column("safe_reason", sa.String(100), nullable=True),
        sa.Column("valued_at", sa.DateTime(timezone=True), nullable=False),
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
            "direction IS NULL OR direction IN ('IN', 'OUT')",
            name="ck_cash_flow_valuations_direction",
        ),
        sa.CheckConstraint(
            "valuation_status IN ('COMPLETE', 'PARTIAL')",
            name="ck_cash_flow_valuations_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_activity_id"], ["account_activities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_activity_id"),
    )
    op.create_index(
        "ix_cash_flow_valuations_owner_event",
        "account_cash_flow_valuations",
        ["user_id", "exchange", "event_time"],
    )
    op.create_index(
        op.f("ix_account_cash_flow_valuations_user_id"),
        "account_cash_flow_valuations",
        ["user_id"],
    )
    op.create_index(
        op.f("ix_account_cash_flow_valuations_valuation_status"),
        "account_cash_flow_valuations",
        ["valuation_status"],
    )

    op.create_table(
        "portfolio_performance_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("portfolio_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_portfolio_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("period_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_value_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("end_value_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("external_inflow_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("external_outflow_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("net_external_flow_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("return_method", sa.String(30), nullable=False),
        sa.Column("period_return_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("cumulative_return_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("performance_index", sa.Numeric(30, 10), nullable=True),
        sa.Column("high_water_mark_index", sa.Numeric(30, 10), nullable=True),
        sa.Column("high_water_mark_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("drawdown_index", sa.Numeric(30, 10), nullable=True),
        sa.Column("drawdown_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("drawdown_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("max_drawdown_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("performance_status", sa.String(20), nullable=False),
        sa.Column("safe_reason", sa.String(100), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
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
            "performance_status IN ('BASELINE', 'COMPLETE', 'PARTIAL')",
            name="ck_portfolio_performance_status",
        ),
        sa.CheckConstraint(
            "return_method = 'MODIFIED_DIETZ'",
            name="ck_portfolio_performance_return_method",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["previous_portfolio_snapshot_id"],
            ["portfolio_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_snapshot_id"),
    )
    op.create_index(
        "ix_portfolio_performance_owner_period",
        "portfolio_performance_snapshots",
        ["user_id", "exchange", "period_end_at"],
    )
    op.create_index(
        op.f("ix_portfolio_performance_snapshots_user_id"),
        "portfolio_performance_snapshots",
        ["user_id"],
    )
    op.create_index(
        op.f("ix_portfolio_performance_snapshots_performance_status"),
        "portfolio_performance_snapshots",
        ["performance_status"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_portfolio_performance_snapshots_performance_status"),
        table_name="portfolio_performance_snapshots",
    )
    op.drop_index(
        op.f("ix_portfolio_performance_snapshots_user_id"),
        table_name="portfolio_performance_snapshots",
    )
    op.drop_index(
        "ix_portfolio_performance_owner_period",
        table_name="portfolio_performance_snapshots",
    )
    op.drop_table("portfolio_performance_snapshots")
    op.drop_index(
        op.f("ix_account_cash_flow_valuations_valuation_status"),
        table_name="account_cash_flow_valuations",
    )
    op.drop_index(
        op.f("ix_account_cash_flow_valuations_user_id"),
        table_name="account_cash_flow_valuations",
    )
    op.drop_index(
        "ix_cash_flow_valuations_owner_event",
        table_name="account_cash_flow_valuations",
    )
    op.drop_table("account_cash_flow_valuations")
