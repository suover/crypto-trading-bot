"""add shadow policy enrollments

Revision ID: a5c7e9f1b3d4
Revises: f4a8c2e6b1d3
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a5c7e9f1b3d4"
down_revision: str | Sequence[str] | None = "f4a8c2e6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_policy_enrollments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("enrollment_schema_version", sa.String(50), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
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
        sa.Column(
            "candidate_registered_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "candidate_registration_snapshot_id_watermark",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "candidate_registration_captured_at_watermark",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("gate_result_type", sa.String(80), nullable=False),
        sa.Column("gate_policy_schema_version", sa.String(50), nullable=False),
        sa.Column("gate_policy_signature", sa.String(100), nullable=False),
        sa.Column(
            "gate_policy_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("gate_status", sa.String(40), nullable=False),
        sa.Column("gate_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gate_forward_snapshot_id_ceiling", sa.BigInteger(), nullable=False),
        sa.Column(
            "gate_checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "gate_evidence_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("gate_decision_signature", sa.String(100), nullable=False),
        sa.Column("shadow_enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shadow_snapshot_id_watermark", sa.BigInteger(), nullable=False),
        sa.Column(
            "shadow_captured_at_watermark", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_shadow_enrollment_top_n_positive"
        ),
        sa.CheckConstraint(
            "candidate_registration_snapshot_id_watermark > 0",
            name="ck_shadow_enrollment_candidate_watermark_positive",
        ),
        sa.CheckConstraint(
            "gate_forward_snapshot_id_ceiling > 0",
            name="ck_shadow_enrollment_gate_ceiling_positive",
        ),
        sa.CheckConstraint(
            "shadow_snapshot_id_watermark > 0",
            name="ck_shadow_enrollment_shadow_watermark_positive",
        ),
        sa.CheckConstraint(
            "gate_status = 'ELIGIBLE_FOR_REVIEW'",
            name="ck_shadow_enrollment_gate_status_eligible",
        ),
        sa.CheckConstraint(
            "shadow_snapshot_id_watermark >= gate_forward_snapshot_id_ceiling",
            name="ck_shadow_enrollment_watermark_after_gate",
        ),
        sa.CheckConstraint(
            "shadow_enrolled_at >= gate_evaluated_at",
            name="ck_shadow_enrollment_time_after_gate",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_shadow_enrollment_candidate"),
    )
    op.create_index(
        "ix_shadow_enrollment_context_time",
        "shadow_policy_enrollments",
        ["user_id", "exchange", "quote_asset", "shadow_enrolled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shadow_enrollment_context_time",
        table_name="shadow_policy_enrollments",
    )
    op.drop_table("shadow_policy_enrollments")
