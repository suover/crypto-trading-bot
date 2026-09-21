"""add dynamic market universe

Revision ID: 8e4f1b2c9d6a
Revises: f7a1c2d3e4b5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "8e4f1b2c9d6a"
down_revision: str | Sequence[str] | None = "f7a1c2d3e4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("pipeline_run_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_analysis_runs_pipeline_run_id"),
        "analysis_runs",
        ["pipeline_run_id"],
        unique=False,
    )
    op.create_table(
        "market_universe_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=False),
        sa.Column("base_asset", sa.String(length=20), nullable=False),
        sa.Column("quote_asset", sa.String(length=20), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(precision=18, scale=9), nullable=True),
        sa.Column("selection_source", sa.String(length=30), nullable=False),
        sa.Column("buy_eligible", sa.Boolean(), nullable=False),
        sa.Column("sell_eligible", sa.Boolean(), nullable=False),
        sa.Column(
            "quote_trade_value_24h", sa.Numeric(precision=30, scale=2), nullable=True
        ),
        sa.Column("market_event_data", postgresql.JSONB(), nullable=True),
        sa.Column("feature_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id",
            "exchange",
            "market",
            name="uq_universe_candidates_run_exchange_market",
        ),
    )
    op.create_index(
        op.f("ix_market_universe_candidates_analysis_run_id"),
        "market_universe_candidates",
        ["analysis_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_market_universe_candidates_market"),
        "market_universe_candidates",
        ["market"],
        unique=False,
    )
    op.create_index(
        op.f("ix_market_universe_candidates_user_id"),
        "market_universe_candidates",
        ["user_id"],
        unique=False,
    )
    op.add_column(
        "trade_recommendations",
        sa.Column("universe_candidate_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_trade_recommendations_universe_candidate_id",
        "trade_recommendations",
        "market_universe_candidates",
        ["universe_candidate_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_trade_recommendations_universe_candidate_id"),
        "trade_recommendations",
        ["universe_candidate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_trade_recommendations_universe_candidate_id"),
        table_name="trade_recommendations",
    )
    op.drop_constraint(
        "fk_trade_recommendations_universe_candidate_id",
        "trade_recommendations",
        type_="foreignkey",
    )
    op.drop_column("trade_recommendations", "universe_candidate_id")
    op.drop_index(
        op.f("ix_market_universe_candidates_user_id"),
        table_name="market_universe_candidates",
    )
    op.drop_index(
        op.f("ix_market_universe_candidates_market"),
        table_name="market_universe_candidates",
    )
    op.drop_index(
        op.f("ix_market_universe_candidates_analysis_run_id"),
        table_name="market_universe_candidates",
    )
    op.drop_table("market_universe_candidates")
    op.drop_index(op.f("ix_analysis_runs_pipeline_run_id"), table_name="analysis_runs")
    op.drop_column("analysis_runs", "pipeline_run_id")
