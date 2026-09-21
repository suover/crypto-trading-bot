"""add portfolio valuation snapshots

Revision ID: c3a8d1e5f7b9
Revises: 9b7d4e6f2a10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c3a8d1e5f7b9"
down_revision: str | Sequence[str] | None = "9b7d4e6f2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("quote_asset", sa.String(length=20), nullable=False),
        sa.Column("cash_available_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("cash_locked_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("cash_total_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("priced_positions_value_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("known_total_value_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("total_value_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column(
            "positions_estimated_cost_basis_krw",
            sa.Numeric(30, 10),
            nullable=True,
        ),
        sa.Column("unrealized_pnl_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("unrealized_pnl_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("unpriced_asset_count", sa.Integer(), nullable=False),
        sa.Column("missing_cost_basis_count", sa.Integer(), nullable=False),
        sa.Column("valuation_status", sa.String(length=20), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_run_id"),
        sa.UniqueConstraint(
            "user_id",
            "exchange",
            "pipeline_run_id",
            name="uq_portfolio_snapshots_user_exchange_pipeline",
        ),
    )
    op.create_index(
        op.f("ix_portfolio_snapshots_pipeline_run_id"),
        "portfolio_snapshots",
        ["pipeline_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_portfolio_snapshots_user_id"),
        "portfolio_snapshots",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "portfolio_position_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("portfolio_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("account_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("market_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=True),
        sa.Column("currency", sa.String(length=20), nullable=False),
        sa.Column("available_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("locked_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("total_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("avg_buy_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("mark_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("market_value_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("estimated_cost_basis_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("unrealized_pnl_krw", sa.Numeric(30, 10), nullable=True),
        sa.Column("unrealized_pnl_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("valuation_status", sa.String(length=20), nullable=False),
        sa.Column("price_source", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_snapshot_id"], ["account_snapshots.id"]),
        sa.ForeignKeyConstraint(["market_snapshot_id"], ["market_snapshots.id"]),
        sa.ForeignKeyConstraint(
            ["portfolio_snapshot_id"],
            ["portfolio_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "portfolio_snapshot_id",
            "currency",
            name="uq_portfolio_positions_snapshot_currency",
        ),
    )
    for column_name in (
        "account_snapshot_id",
        "market_snapshot_id",
        "portfolio_snapshot_id",
    ):
        op.create_index(
            op.f(f"ix_portfolio_position_snapshots_{column_name}"),
            "portfolio_position_snapshots",
            [column_name],
            unique=False,
        )


def downgrade() -> None:
    for column_name in (
        "portfolio_snapshot_id",
        "market_snapshot_id",
        "account_snapshot_id",
    ):
        op.drop_index(
            op.f(f"ix_portfolio_position_snapshots_{column_name}"),
            table_name="portfolio_position_snapshots",
        )
    op.drop_table("portfolio_position_snapshots")
    op.drop_index(
        op.f("ix_portfolio_snapshots_user_id"), table_name="portfolio_snapshots"
    )
    op.drop_index(
        op.f("ix_portfolio_snapshots_pipeline_run_id"),
        table_name="portfolio_snapshots",
    )
    op.drop_table("portfolio_snapshots")
