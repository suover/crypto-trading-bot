"""add bot trading pnl accounting

Revision ID: e6f2a9b4c7d1
Revises: c3a8d1e5f7b9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e6f2a9b4c7d1"
down_revision: str | Sequence[str] | None = "c3a8d1e5f7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_inventory_lots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=False),
        sa.Column("source_buy_order_log_id", sa.BigInteger(), nullable=False),
        sa.Column("acquired_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("gross_buy_funds_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("buy_fee_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("original_cost_basis_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("remaining_cost_basis_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("unit_cost_basis_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "acquired_quantity > 0 AND remaining_quantity >= 0",
            name="ck_bot_inventory_lots_quantities",
        ),
        sa.CheckConstraint(
            "gross_buy_funds_krw > 0 AND buy_fee_krw >= 0",
            name="ck_bot_inventory_lots_buy_values",
        ),
        sa.CheckConstraint(
            "original_cost_basis_krw > 0 AND remaining_cost_basis_krw >= 0",
            name="ck_bot_inventory_lots_cost_basis",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["source_buy_order_log_id"], ["order_logs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_buy_order_log_id", name="uq_bot_inventory_lots_source_buy_order"
        ),
    )
    op.create_index(
        op.f("ix_bot_inventory_lots_user_id"),
        "bot_inventory_lots",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_bot_inventory_lots_scope_fifo",
        "bot_inventory_lots",
        ["user_id", "exchange", "market", "opened_at", "source_buy_order_log_id"],
        unique=False,
    )

    op.create_table(
        "bot_sell_realizations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=False),
        sa.Column("source_sell_order_log_id", sa.BigInteger(), nullable=False),
        sa.Column("sold_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("gross_sell_proceeds_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("sell_fee_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("matched_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("unmatched_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("recognized_gross_proceeds_krw", sa.Numeric(30, 10)),
        sa.Column("recognized_sell_fee_krw", sa.Numeric(30, 10)),
        sa.Column("recognized_net_proceeds_krw", sa.Numeric(30, 10)),
        sa.Column("recognized_cost_basis_krw", sa.Numeric(30, 10)),
        sa.Column("recognized_realized_pnl_krw", sa.Numeric(30, 10)),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "attribution_method",
            sa.String(length=30),
            server_default=sa.text("'BOT_FIFO'"),
            nullable=False,
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
            "status IN ('FULLY_MATCHED', 'PARTIALLY_MATCHED', 'UNMATCHED')",
            name="ck_bot_sell_realizations_status",
        ),
        sa.CheckConstraint(
            "sold_quantity > 0 AND matched_quantity >= 0 AND unmatched_quantity >= 0 "
            "AND matched_quantity + unmatched_quantity = sold_quantity",
            name="ck_bot_sell_realizations_quantities",
        ),
        sa.CheckConstraint(
            "gross_sell_proceeds_krw > 0 AND sell_fee_krw >= 0",
            name="ck_bot_sell_realizations_sell_values",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["source_sell_order_log_id"], ["order_logs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_sell_order_log_id",
            name="uq_bot_sell_realizations_source_sell_order",
        ),
    )
    op.create_index(
        op.f("ix_bot_sell_realizations_user_id"),
        "bot_sell_realizations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_bot_sell_realizations_scope_order",
        "bot_sell_realizations",
        ["user_id", "exchange", "market", "source_sell_order_log_id"],
        unique=False,
    )

    op.create_table(
        "bot_pnl_matches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sell_realization_id", sa.BigInteger(), nullable=False),
        sa.Column("buy_lot_id", sa.BigInteger(), nullable=False),
        sa.Column("matched_quantity", sa.Numeric(30, 10), nullable=False),
        sa.Column("allocated_buy_cost_basis_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column(
            "allocated_sell_gross_proceeds_krw", sa.Numeric(30, 10), nullable=False
        ),
        sa.Column("allocated_sell_fee_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column(
            "allocated_sell_net_proceeds_krw", sa.Numeric(30, 10), nullable=False
        ),
        sa.Column("realized_pnl_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "matched_quantity > 0 AND allocated_buy_cost_basis_krw >= 0",
            name="ck_bot_pnl_matches_quantity_cost",
        ),
        sa.ForeignKeyConstraint(
            ["sell_realization_id"],
            ["bot_sell_realizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["buy_lot_id"], ["bot_inventory_lots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sell_realization_id", "buy_lot_id", name="uq_bot_pnl_matches_sell_lot"
        ),
    )
    op.create_index(
        op.f("ix_bot_pnl_matches_sell_realization_id"),
        "bot_pnl_matches",
        ["sell_realization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bot_pnl_matches_buy_lot_id"),
        "bot_pnl_matches",
        ["buy_lot_id"],
        unique=False,
    )

    op.create_table(
        "bot_trading_pnl_summaries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("processed_order_count", sa.Integer(), nullable=False),
        sa.Column("processed_buy_order_count", sa.Integer(), nullable=False),
        sa.Column("processed_sell_order_count", sa.Integer(), nullable=False),
        sa.Column("gross_buy_funds_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("gross_sell_funds_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("total_buy_fees_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("total_sell_fees_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("total_fees_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("recognized_sell_proceeds_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("recognized_cost_basis_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("recognized_realized_pnl_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column(
            "recognized_realized_return_percentage", sa.Numeric(30, 10), nullable=True
        ),
        sa.Column("open_bot_cost_basis_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("open_bot_lot_count", sa.Integer(), nullable=False),
        sa.Column("fully_matched_sell_count", sa.Integer(), nullable=False),
        sa.Column("partially_matched_sell_count", sa.Integer(), nullable=False),
        sa.Column("unmatched_sell_count", sa.Integer(), nullable=False),
        sa.Column("winning_sell_count", sa.Integer(), nullable=False),
        sa.Column("losing_sell_count", sa.Integer(), nullable=False),
        sa.Column("breakeven_sell_count", sa.Integer(), nullable=False),
        sa.Column("win_rate_percentage", sa.Numeric(30, 10), nullable=True),
        sa.Column("incomplete_order_count", sa.Integer(), nullable=False),
        sa.Column("accounting_status", sa.String(length=20), nullable=False),
        sa.Column("source_order_count", sa.Integer(), nullable=False),
        sa.Column("source_signature", sa.String(length=64), nullable=False),
        sa.Column("source_last_updated_at", sa.DateTime(timezone=True), nullable=True),
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
            "accounting_status IN ('COMPLETE', 'PARTIAL')",
            name="ck_bot_trading_pnl_summaries_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "exchange", name="uq_bot_trading_pnl_summaries_user_exchange"
        ),
    )
    op.create_index(
        op.f("ix_bot_trading_pnl_summaries_user_id"),
        "bot_trading_pnl_summaries",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_bot_trading_pnl_summaries_user_id"),
        table_name="bot_trading_pnl_summaries",
    )
    op.drop_table("bot_trading_pnl_summaries")
    op.drop_index(op.f("ix_bot_pnl_matches_buy_lot_id"), table_name="bot_pnl_matches")
    op.drop_index(
        op.f("ix_bot_pnl_matches_sell_realization_id"),
        table_name="bot_pnl_matches",
    )
    op.drop_table("bot_pnl_matches")
    op.drop_index(
        "ix_bot_sell_realizations_scope_order",
        table_name="bot_sell_realizations",
    )
    op.drop_index(
        op.f("ix_bot_sell_realizations_user_id"),
        table_name="bot_sell_realizations",
    )
    op.drop_table("bot_sell_realizations")
    op.drop_index("ix_bot_inventory_lots_scope_fifo", table_name="bot_inventory_lots")
    op.drop_index(
        op.f("ix_bot_inventory_lots_user_id"), table_name="bot_inventory_lots"
    )
    op.drop_table("bot_inventory_lots")
