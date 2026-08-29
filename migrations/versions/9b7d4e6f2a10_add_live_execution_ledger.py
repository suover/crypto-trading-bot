"""add live execution ledger

Revision ID: 9b7d4e6f2a10
Revises: 8e4f1b2c9d6a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "9b7d4e6f2a10"
down_revision: str | Sequence[str] | None = "8e4f1b2c9d6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column_name in (
        "executed_quantity",
        "executed_funds_krw",
        "average_execution_price",
        "paid_fee",
        "remaining_quantity",
    ):
        op.add_column(
            "order_logs",
            sa.Column(column_name, sa.Numeric(precision=30, scale=10), nullable=True),
        )
    op.add_column("order_logs", sa.Column("trades_count", sa.Integer(), nullable=True))
    op.add_column(
        "order_logs",
        sa.Column("execution_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "order_fills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_log_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange_trade_id", sa.String(length=100), nullable=False),
        sa.Column("price", sa.Numeric(precision=30, scale=10), nullable=False),
        sa.Column("volume", sa.Numeric(precision=30, scale=10), nullable=False),
        sa.Column("funds_krw", sa.Numeric(precision=30, scale=10), nullable=False),
        sa.Column("side", sa.String(length=20), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_log_id"], ["order_logs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_log_id",
            "exchange_trade_id",
            name="uq_order_fills_order_log_exchange_trade",
        ),
    )
    op.create_index(
        op.f("ix_order_fills_order_log_id"),
        "order_fills",
        ["order_log_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_order_fills_order_log_id"), table_name="order_fills")
    op.drop_table("order_fills")
    op.drop_column("order_logs", "execution_synced_at")
    op.drop_column("order_logs", "trades_count")
    for column_name in (
        "remaining_quantity",
        "paid_fee",
        "average_execution_price",
        "executed_funds_krw",
        "executed_quantity",
    ):
        op.drop_column("order_logs", column_name)
