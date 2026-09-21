"""add exchange market registry

Revision ID: c5e7a9b1d3f4
Revises: b880dce00b53
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5e7a9b1d3f4"
down_revision: Union[str, Sequence[str], None] = "b880dce00b53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exchanges",
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "tradable", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("default_quote_asset", sa.String(length=20), nullable=True),
        sa.Column(
            "default_max_order_amount", sa.Numeric(precision=20, scale=2), nullable=True
        ),
        sa.Column(
            "default_daily_max_order_amount",
            sa.Numeric(precision=20, scale=2),
            nullable=True,
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
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "exchange_markets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("exchange_code", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=False),
        sa.Column("base_asset", sa.String(length=20), nullable=False),
        sa.Column("quote_asset", sa.String(length=20), nullable=False),
        sa.Column("coingecko_id", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "priority", sa.Integer(), server_default=sa.text("100"), nullable=False
        ),
        sa.Column(
            "max_order_amount_override",
            sa.Numeric(precision=20, scale=2),
            nullable=True,
        ),
        sa.Column(
            "daily_max_order_amount_override",
            sa.Numeric(precision=20, scale=2),
            nullable=True,
        ),
        sa.Column(
            "min_24h_quote_volume", sa.Numeric(precision=30, scale=10), nullable=True
        ),
        sa.Column("exclude_reason", sa.Text(), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["exchange_code"], ["exchanges.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exchange_code", "market", name="uq_exchange_markets_exchange_market"
        ),
    )
    op.create_index(
        op.f("ix_exchange_markets_exchange_code"),
        "exchange_markets",
        ["exchange_code"],
        unique=False,
    )

    exchanges = sa.table(
        "exchanges",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("tradable", sa.Boolean),
        sa.column("default_quote_asset", sa.String),
        sa.column("default_max_order_amount", sa.Numeric),
        sa.column("default_daily_max_order_amount", sa.Numeric),
    )
    exchange_markets = sa.table(
        "exchange_markets",
        sa.column("exchange_code", sa.String),
        sa.column("market", sa.String),
        sa.column("base_asset", sa.String),
        sa.column("quote_asset", sa.String),
        sa.column("coingecko_id", sa.String),
        sa.column("status", sa.String),
        sa.column("priority", sa.Integer),
    )
    op.bulk_insert(
        exchanges,
        [
            {
                "code": "UPBIT",
                "name": "Upbit",
                "enabled": True,
                "tradable": True,
                "default_quote_asset": "KRW",
                "default_max_order_amount": 10000,
                "default_daily_max_order_amount": 100000,
            }
        ],
    )
    op.bulk_insert(
        exchange_markets,
        [
            {
                "exchange_code": "UPBIT",
                "market": "KRW-BTC",
                "base_asset": "BTC",
                "quote_asset": "KRW",
                "coingecko_id": "bitcoin",
                "status": "ACTIVE",
                "priority": 1,
            },
            {
                "exchange_code": "UPBIT",
                "market": "KRW-ETH",
                "base_asset": "ETH",
                "quote_asset": "KRW",
                "coingecko_id": "ethereum",
                "status": "ACTIVE",
                "priority": 2,
            },
            {
                "exchange_code": "UPBIT",
                "market": "KRW-XRP",
                "base_asset": "XRP",
                "quote_asset": "KRW",
                "coingecko_id": "ripple",
                "status": "ACTIVE",
                "priority": 3,
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM exchange_markets "
            "WHERE exchange_code = 'UPBIT' "
            "AND market IN ('KRW-BTC', 'KRW-ETH', 'KRW-XRP')"
        )
    )
    op.execute(sa.text("DELETE FROM exchanges WHERE code = 'UPBIT'"))
    op.drop_index(
        op.f("ix_exchange_markets_exchange_code"), table_name="exchange_markets"
    )
    op.drop_table("exchange_markets")
    op.drop_table("exchanges")
