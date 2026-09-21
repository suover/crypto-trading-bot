"""add full live policy activation

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "full_live_policy_activations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("activation_schema_version", sa.String(50), nullable=False),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("promotion_approval_signature", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("shadow_enrollment_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("scenario_name", sa.String(80), nullable=False),
        sa.Column("scenario_definition_signature", sa.String(100), nullable=False),
        sa.Column("component_weights", postgresql.JSONB(), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_policy_definition", postgresql.JSONB(), nullable=False),
        sa.Column("effective_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_top_n", sa.Integer(), nullable=False),
        sa.Column("activation_source", sa.String(30), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activation_signature", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_full_live_activation_top_n_positive"
        ),
        sa.CheckConstraint(
            "activation_source = 'MANUAL_CLI'",
            name="ck_full_live_activation_source_manual",
        ),
        sa.ForeignKeyConstraint(
            ["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(
            ["shadow_enrollment_id"], ["shadow_policy_enrollments.id"]
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "promotion_approval_id", name="uq_full_live_activation_approval"
        ),
    )
    op.create_index(
        "ix_full_live_activation_context_time",
        "full_live_policy_activations",
        ["user_id", "exchange", "quote_asset", "activated_at"],
    )
    op.create_table(
        "full_live_policy_termination_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("termination_schema_version", sa.String(50), nullable=False),
        sa.Column("activation_id", sa.BigInteger(), nullable=False),
        sa.Column("activation_signature", sa.String(100), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("termination_source", sa.String(30), nullable=False),
        sa.Column("termination_reason", sa.Text(), nullable=False),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("termination_signature", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "termination_source = 'MANUAL_CLI'",
            name="ck_full_live_termination_source_manual",
        ),
        sa.ForeignKeyConstraint(["activation_id"], ["full_live_policy_activations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "activation_id", name="uq_full_live_termination_activation"
        ),
    )
    op.create_index(
        "ix_full_live_termination_context_time",
        "full_live_policy_termination_events",
        ["user_id", "exchange", "quote_asset", "terminated_at"],
    )
    op.create_table(
        "market_universe_policy_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("policy_run_schema_version", sa.String(50), nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_policy_signature", sa.String(100), nullable=False),
        sa.Column("full_live_activation_id", sa.BigInteger(), nullable=True),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('BASELINE', 'FULL_LIVE')",
            name="ck_market_universe_policy_run_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'BASELINE' AND full_live_activation_id IS NULL "
            "AND promotion_approval_id IS NULL) OR "
            "(mode = 'FULL_LIVE' AND full_live_activation_id IS NOT NULL "
            "AND promotion_approval_id IS NOT NULL)",
            name="ck_market_universe_policy_run_provenance",
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["full_live_activation_id"], ["full_live_policy_activations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_run_id"),
    )
    op.create_index(
        "ix_market_universe_policy_run_context_time",
        "market_universe_policy_runs",
        ["user_id", "exchange", "quote_asset", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_universe_policy_run_context_time",
        table_name="market_universe_policy_runs",
    )
    op.drop_table("market_universe_policy_runs")
    op.drop_index(
        "ix_full_live_termination_context_time",
        table_name="full_live_policy_termination_events",
    )
    op.drop_table("full_live_policy_termination_events")
    op.drop_index(
        "ix_full_live_activation_context_time",
        table_name="full_live_policy_activations",
    )
    op.drop_table("full_live_policy_activations")
