"""add shadow policy evaluations

Revision ID: b6d8f0a2c4e5
Revises: a5c7e9f1b3d4
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b6d8f0a2c4e5"
down_revision: str | Sequence[str] | None = "a5c7e9f1b3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_policy_evaluations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_schema_version", sa.String(50), nullable=False),
        sa.Column("shadow_enrollment_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("gate_decision_signature", sa.String(100), nullable=False),
        sa.Column("shadow_enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shadow_snapshot_id_watermark", sa.BigInteger(), nullable=False),
        sa.Column(
            "shadow_captured_at_watermark",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("strategy_replay_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("snapshot_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset_schema_version", sa.String(50), nullable=False),
        sa.Column("snapshot_policy_signature", sa.String(100), nullable=False),
        sa.Column("snapshot_stored_top_n", sa.Integer(), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_top_n", sa.Integer(), nullable=False),
        sa.Column("scenario_name", sa.String(80), nullable=False),
        sa.Column("scenario_definition_signature", sa.String(100), nullable=False),
        sa.Column("scenario_signature", sa.String(100), nullable=True),
        sa.Column("context_matches_enrollment", sa.Boolean(), nullable=False),
        sa.Column("replay_status", sa.String(40), nullable=False),
        sa.Column("baseline_matches_stored", sa.Boolean(), nullable=False),
        sa.Column(
            "baseline_top_markets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "shadow_top_markets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("top_n_overlap_count", sa.Integer(), nullable=False),
        sa.Column("top_n_overlap_rate", sa.Numeric(38, 28), nullable=False),
        sa.Column(
            "entered_top_n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "exited_top_n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("rankable_candidate_count", sa.Integer(), nullable=False),
        sa.Column("evaluation_status", sa.String(40), nullable=False),
        sa.Column("safe_reason", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_signature", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_shadow_evaluation_top_n_positive"
        ),
        sa.CheckConstraint(
            "top_n_overlap_count >= 0 AND top_n_overlap_count <= effective_top_n",
            name="ck_shadow_evaluation_overlap_count",
        ),
        sa.CheckConstraint(
            "top_n_overlap_rate >= 0 AND top_n_overlap_rate <= 1",
            name="ck_shadow_evaluation_overlap_rate",
        ),
        sa.CheckConstraint(
            "evaluation_status IN ('SUCCESS', 'CONTEXT_MISMATCH', "
            "'BASELINE_INTEGRITY_FAILED', 'REPLAY_INCOMPATIBLE')",
            name="ck_shadow_evaluation_status",
        ),
        sa.CheckConstraint(
            "strategy_replay_snapshot_id > shadow_snapshot_id_watermark",
            name="ck_shadow_evaluation_snapshot_after_watermark",
        ),
        sa.CheckConstraint(
            "snapshot_captured_at > shadow_enrolled_at",
            name="ck_shadow_evaluation_snapshot_after_enrollment",
        ),
        sa.CheckConstraint(
            "snapshot_captured_at > shadow_captured_at_watermark",
            name="ck_shadow_evaluation_snapshot_after_captured_watermark",
        ),
        sa.ForeignKeyConstraint(
            ["shadow_enrollment_id"], ["shadow_policy_enrollments.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(
            ["strategy_replay_snapshot_id"], ["strategy_replay_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shadow_enrollment_id",
            "strategy_replay_snapshot_id",
            name="uq_shadow_evaluation_enrollment_snapshot",
        ),
    )
    op.create_index(
        "ix_shadow_evaluation_enrollment_captured",
        "shadow_policy_evaluations",
        ["shadow_enrollment_id", "snapshot_captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_shadow_evaluation_candidate_captured",
        "shadow_policy_evaluations",
        ["candidate_id", "snapshot_captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shadow_evaluation_candidate_captured",
        table_name="shadow_policy_evaluations",
    )
    op.drop_index(
        "ix_shadow_evaluation_enrollment_captured",
        table_name="shadow_policy_evaluations",
    )
    op.drop_table("shadow_policy_evaluations")
