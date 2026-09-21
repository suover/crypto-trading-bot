"""add research candidate outcomes

Revision ID: e2f4a6b8c0d1
Revises: d1e3f5a7b9c2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e2f4a6b8c0d1"
down_revision: str | Sequence[str] | None = "d1e3f5a7b9c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_replay_candidate_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("strategy_replay_candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_replay_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("reference_price_source", sa.String(80), nullable=False),
        sa.Column("end_price", sa.Numeric(30, 10), nullable=True),
        sa.Column("end_price_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_price_source", sa.String(50), nullable=False),
        sa.Column("market_return_percentage", sa.Numeric(30, 12), nullable=True),
        sa.Column("evaluation_status", sa.String(20), nullable=False),
        sa.Column("safe_reason", sa.String(100), nullable=True),
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
            ["strategy_replay_candidate_id"],
            ["strategy_replay_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_replay_snapshot_id"],
            ["strategy_replay_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "strategy_replay_candidate_id",
            "horizon_minutes",
            name="uq_strategy_replay_candidate_outcomes_candidate_horizon",
        ),
    )
    op.create_index(
        "ix_srco_candidate_id",
        "strategy_replay_candidate_outcomes",
        ["strategy_replay_candidate_id"],
    )
    op.create_index(
        "ix_srco_snapshot_id",
        "strategy_replay_candidate_outcomes",
        ["strategy_replay_snapshot_id"],
    )
    op.create_index(
        "ix_srco_user_id",
        "strategy_replay_candidate_outcomes",
        ["user_id"],
    )
    op.create_index(
        "ix_srco_user_target",
        "strategy_replay_candidate_outcomes",
        ["user_id", "target_at"],
    )
    op.create_index(
        "ix_srco_status_target",
        "strategy_replay_candidate_outcomes",
        ["evaluation_status", "target_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_srco_status_target",
        table_name="strategy_replay_candidate_outcomes",
    )
    op.drop_index(
        "ix_srco_user_target",
        table_name="strategy_replay_candidate_outcomes",
    )
    op.drop_index(
        "ix_srco_user_id",
        table_name="strategy_replay_candidate_outcomes",
    )
    op.drop_index(
        "ix_srco_snapshot_id",
        table_name="strategy_replay_candidate_outcomes",
    )
    op.drop_index(
        "ix_srco_candidate_id",
        table_name="strategy_replay_candidate_outcomes",
    )
    op.drop_table("strategy_replay_candidate_outcomes")
