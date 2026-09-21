"""add shadow promotion approvals

Revision ID: c7e9a1b3d5f2
Revises: b6d8f0a2c4e5
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c7e9a1b3d5f2"
down_revision: str | Sequence[str] | None = "b6d8f0a2c4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_policy_promotion_approvals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_schema_version", sa.String(50), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("shadow_enrollment_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_schema_version", sa.String(50), nullable=False),
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
        sa.Column("effective_top_n", sa.Integer(), nullable=False),
        sa.Column("shadow_enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shadow_snapshot_id_watermark", sa.BigInteger(), nullable=False),
        sa.Column(
            "shadow_captured_at_watermark",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("pre_shadow_gate_decision_signature", sa.String(100), nullable=False),
        sa.Column("review_result_type", sa.String(80), nullable=False),
        sa.Column("review_policy_schema_version", sa.String(50), nullable=False),
        sa.Column("review_policy_signature", sa.String(100), nullable=False),
        sa.Column(
            "review_policy_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("review_status", sa.String(50), nullable=False),
        sa.Column("review_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_decision_signature", sa.String(100), nullable=False),
        sa.Column(
            "review_decision_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "performance_evidence_as_of",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "shadow_evaluation_snapshot_id_ceiling", sa.BigInteger(), nullable=False
        ),
        sa.Column(
            "review_checks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "review_evidence_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("approval_source", sa.String(30), nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_signature", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_shadow_promotion_top_n_positive"
        ),
        sa.CheckConstraint(
            "review_status = 'ELIGIBLE_FOR_PROMOTION_REVIEW'",
            name="ck_shadow_promotion_review_eligible",
        ),
        sa.CheckConstraint(
            "approval_source = 'MANUAL_CLI'",
            name="ck_shadow_promotion_source_manual",
        ),
        sa.CheckConstraint(
            "shadow_evaluation_snapshot_id_ceiling > 0",
            name="ck_shadow_promotion_ceiling_positive",
        ),
        sa.CheckConstraint(
            "human_approved_at >= review_evaluated_at",
            name="ck_shadow_promotion_time_after_review",
        ),
        sa.CheckConstraint(
            "human_approved_at >= performance_evidence_as_of",
            name="ck_shadow_promotion_time_after_evidence",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(
            ["shadow_enrollment_id"], ["shadow_policy_enrollments.id"]
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_shadow_promotion_candidate"),
        sa.UniqueConstraint(
            "shadow_enrollment_id", name="uq_shadow_promotion_enrollment"
        ),
    )
    op.create_index(
        "ix_shadow_promotion_context_time",
        "shadow_policy_promotion_approvals",
        ["user_id", "exchange", "quote_asset", "human_approved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shadow_promotion_context_time",
        table_name="shadow_policy_promotion_approvals",
    )
    op.drop_table("shadow_policy_promotion_approvals")
