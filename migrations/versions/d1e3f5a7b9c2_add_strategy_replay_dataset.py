"""add strategy replay dataset

Revision ID: d1e3f5a7b9c2
Revises: c9d2e4f6a8b1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d1e3f5a7b9c2"
down_revision: str | Sequence[str] | None = "c9d2e4f6a8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_replay_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("dataset_schema_version", sa.String(50), nullable=False),
        sa.Column("policy_signature", sa.String(100), nullable=False),
        sa.Column(
            "policy_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("research_candidate_count", sa.Integer(), nullable=False),
        sa.Column("prefilter_candidate_count", sa.Integer(), nullable=False),
        sa.Column("ranked_candidate_count", sa.Integer(), nullable=False),
        sa.Column("final_candidate_count", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id", name="uq_strategy_replay_snapshots_analysis_run"
        ),
    )
    op.create_index(
        "ix_strategy_replay_snapshots_pipeline_run_id",
        "strategy_replay_snapshots",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_strategy_replay_snapshots_user_id", "strategy_replay_snapshots", ["user_id"]
    )
    op.create_index(
        "ix_strategy_replay_snapshots_policy_signature",
        "strategy_replay_snapshots",
        ["policy_signature"],
    )

    op.create_table(
        "strategy_replay_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("strategy_replay_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("base_asset", sa.String(20), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("in_prefilter", sa.Boolean(), nullable=False),
        sa.Column("prefilter_rank", sa.Integer(), nullable=True),
        sa.Column("held", sa.Boolean(), nullable=False),
        sa.Column("buy_eligible", sa.Boolean(), nullable=False),
        sa.Column("sell_eligible", sa.Boolean(), nullable=False),
        sa.Column("trading_supported", sa.Boolean(), nullable=False),
        sa.Column("original_rank", sa.Integer(), nullable=True),
        sa.Column("original_score", sa.Numeric(18, 9), nullable=True),
        sa.Column("final_selected", sa.Boolean(), nullable=False),
        sa.Column("final_rank", sa.Integer(), nullable=True),
        sa.Column("selection_source", sa.String(30), nullable=True),
        sa.Column("quote_trade_value_24h", sa.Numeric(30, 2), nullable=True),
        sa.Column(
            "feature_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_replay_snapshot_id"],
            ["strategy_replay_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "strategy_replay_snapshot_id",
            "exchange",
            "market",
            name="uq_strategy_replay_candidates_snapshot_exchange_market",
        ),
    )
    op.create_index(
        "ix_strategy_replay_candidates_snapshot_id",
        "strategy_replay_candidates",
        ["strategy_replay_snapshot_id"],
    )
    op.create_index(
        "ix_strategy_replay_candidates_analysis_run_id",
        "strategy_replay_candidates",
        ["analysis_run_id"],
    )
    op.create_index(
        "ix_strategy_replay_candidates_user_id",
        "strategy_replay_candidates",
        ["user_id"],
    )
    op.create_index(
        "ix_strategy_replay_candidates_market", "strategy_replay_candidates", ["market"]
    )


def downgrade() -> None:
    op.drop_table("strategy_replay_candidates")
    op.drop_table("strategy_replay_snapshots")
