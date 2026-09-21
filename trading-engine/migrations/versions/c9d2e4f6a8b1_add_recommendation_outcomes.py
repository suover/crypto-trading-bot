"""add recommendation outcome evaluation

Revision ID: c9d2e4f6a8b1
Revises: b8c2d4e6f1a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d2e4f6a8b1"
down_revision: str | Sequence[str] | None = "b8c2d4e6f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status_columns = [
        sa.Column("evaluation_status", sa.String(20), nullable=False),
        sa.Column("safe_reason", sa.String(100), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]
    op.create_table(
        "trade_recommendation_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("recommendation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("reference_price_source", sa.String(50), nullable=False),
        sa.Column("end_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("end_price_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_price_source", sa.String(50), nullable=False),
        sa.Column("market_return_percentage", sa.Numeric(30, 12), nullable=True),
        sa.Column(
            "action_aligned_return_percentage", sa.Numeric(30, 12), nullable=True
        ),
        sa.Column("directional_result", sa.String(30), nullable=True),
        *status_columns,
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["trade_recommendations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recommendation_id",
            "horizon_minutes",
            name="uq_recommendation_outcomes_recommendation_horizon",
        ),
    )
    op.create_index(
        "ix_recommendation_outcomes_recommendation_id",
        "trade_recommendation_outcomes",
        ["recommendation_id"],
    )
    op.create_index(
        "ix_recommendation_outcomes_evaluation_status",
        "trade_recommendation_outcomes",
        ["evaluation_status"],
    )
    op.create_index(
        "ix_recommendation_outcomes_user_target",
        "trade_recommendation_outcomes",
        ["user_id", "target_at"],
    )

    op.create_table(
        "trade_recommendation_candidate_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=False),
        sa.Column("universe_candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("recommendation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(18, 9), nullable=True),
        sa.Column("selection_source", sa.String(30), nullable=False),
        sa.Column("buy_eligible", sa.Boolean(), nullable=False),
        sa.Column("sell_eligible", sa.Boolean(), nullable=False),
        sa.Column("held", sa.Boolean(), nullable=False),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.Column("reference_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("end_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("end_price_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_price_source", sa.String(50), nullable=False),
        sa.Column("market_return_percentage", sa.Numeric(30, 12), nullable=True),
        sa.Column("evaluation_status", sa.String(20), nullable=False),
        sa.Column("safe_reason", sa.String(100), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["trade_recommendations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["universe_candidate_id"],
            ["market_universe_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recommendation_id",
            "universe_candidate_id",
            "horizon_minutes",
            name="uq_candidate_outcomes_recommendation_candidate_horizon",
        ),
    )
    op.create_index(
        "ix_candidate_outcomes_recommendation_id",
        "trade_recommendation_candidate_outcomes",
        ["recommendation_id"],
    )
    op.create_index(
        "ix_candidate_outcomes_universe_candidate_id",
        "trade_recommendation_candidate_outcomes",
        ["universe_candidate_id"],
    )
    op.create_index(
        "ix_candidate_outcomes_evaluation_status",
        "trade_recommendation_candidate_outcomes",
        ["evaluation_status"],
    )
    op.create_index(
        "ix_candidate_outcomes_user_target",
        "trade_recommendation_candidate_outcomes",
        ["user_id", "target_at"],
    )


def downgrade() -> None:
    op.drop_table("trade_recommendation_candidate_outcomes")
    op.drop_table("trade_recommendation_outcomes")
