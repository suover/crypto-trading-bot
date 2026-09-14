"""add limited live canary registry

Revision ID: d8f0a2c4e6b1
Revises: c7e9a1b3d5f2
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d8f0a2c4e6b1"
down_revision: str | Sequence[str] | None = "c7e9a1b3d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_policy_canary_activations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("canary_schema_version", sa.String(50), nullable=False),
        sa.Column(
            "canary_policy_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("canary_policy_definition_signature", sa.String(100), nullable=False),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("promotion_approval_signature", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("shadow_enrollment_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("scenario_name", sa.String(80), nullable=False),
        sa.Column("scenario_definition_signature", sa.String(100), nullable=False),
        sa.Column(
            "component_weights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("dataset_schema_version", sa.String(50), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("canary_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_top_n", sa.Integer(), nullable=False),
        sa.Column("activation_source", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_analysis_runs", sa.Integer(), nullable=False),
        sa.Column("activation_signature", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_live_canary_activation_top_n_positive"
        ),
        sa.CheckConstraint(
            "max_analysis_runs > 0",
            name="ck_live_canary_activation_max_runs_positive",
        ),
        sa.CheckConstraint(
            "activation_source = 'MANUAL_CLI'",
            name="ck_live_canary_activation_source_manual",
        ),
        sa.CheckConstraint(
            "expires_at > started_at", name="ck_live_canary_activation_time_order"
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
            "promotion_approval_id", name="uq_live_canary_activation_approval"
        ),
    )
    op.create_index(
        "ix_live_canary_activation_context_started",
        "live_policy_canary_activations",
        ["user_id", "exchange", "quote_asset", "started_at"],
        unique=False,
    )

    op.create_table(
        "live_policy_canary_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_schema_version", sa.String(50), nullable=False),
        sa.Column("canary_activation_id", sa.BigInteger(), nullable=False),
        sa.Column("activation_signature", sa.String(100), nullable=False),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("promotion_approval_signature", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("run_ordinal", sa.Integer(), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("canary_policy_signature", sa.String(100), nullable=False),
        sa.Column("used_canary_policy", sa.Boolean(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_signature", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "run_ordinal > 0", name="ck_live_canary_run_ordinal_positive"
        ),
        sa.CheckConstraint(
            "used_canary_policy = true", name="ck_live_canary_run_policy_used"
        ),
        sa.ForeignKeyConstraint(
            ["canary_activation_id"], ["live_policy_canary_activations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_run_id", name="uq_live_canary_run_analysis"),
        sa.UniqueConstraint(
            "canary_activation_id",
            "pipeline_run_id",
            name="uq_live_canary_run_activation_pipeline",
        ),
        sa.UniqueConstraint(
            "canary_activation_id",
            "run_ordinal",
            name="uq_live_canary_run_activation_ordinal",
        ),
    )
    op.create_index(
        "ix_live_canary_run_activation_reserved",
        "live_policy_canary_runs",
        ["canary_activation_id", "reserved_at"],
        unique=False,
    )
    op.create_index(
        "ix_live_canary_run_context_reserved",
        "live_policy_canary_runs",
        ["user_id", "exchange", "quote_asset", "reserved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_live_canary_run_context_reserved", table_name="live_policy_canary_runs"
    )
    op.drop_index(
        "ix_live_canary_run_activation_reserved", table_name="live_policy_canary_runs"
    )
    op.drop_table("live_policy_canary_runs")
    op.drop_index(
        "ix_live_canary_activation_context_started",
        table_name="live_policy_canary_activations",
    )
    op.drop_table("live_policy_canary_activations")
