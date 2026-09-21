"""add human-approved full LIVE promotion approvals

Revision ID: f0c2e4a6b8d1
Revises: e9a1b3c5d7f2
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f0c2e4a6b8d1"
down_revision: str | Sequence[str] | None = "e9a1b3c5d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "full_live_policy_promotion_approvals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_schema_version", sa.String(60), nullable=False),
        sa.Column("canary_activation_id", sa.BigInteger(), nullable=False),
        sa.Column("canary_activation_signature", sa.String(100), nullable=False),
        sa.Column("safety_binding_id", sa.BigInteger(), nullable=False),
        sa.Column("safety_binding_signature", sa.String(110), nullable=False),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("promotion_approval_signature", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("scenario_name", sa.String(80), nullable=False),
        sa.Column("scenario_definition_signature", sa.String(100), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("canary_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_top_n", sa.Integer(), nullable=False),
        sa.Column("termination_event_id", sa.BigInteger(), nullable=True),
        sa.Column("termination_signature", sa.String(110), nullable=True),
        sa.Column("evidence_schema_version", sa.String(50), nullable=False),
        sa.Column("evidence_signature", sa.String(100), nullable=False),
        sa.Column("evidence_as_of", sa.DateTime(timezone=True), nullable=False),
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
            "review_checks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("approval_source", sa.String(30), nullable=False),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_signature", sa.String(110), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval_source = 'MANUAL_CLI'",
            name="ck_full_live_promotion_source_manual",
        ),
        sa.CheckConstraint(
            "review_status = 'ELIGIBLE_FOR_FULL_LIVE_REVIEW'",
            name="ck_full_live_promotion_review_eligible",
        ),
        sa.CheckConstraint(
            "human_approved_at >= review_evaluated_at",
            name="ck_full_live_promotion_time_after_review",
        ),
        sa.CheckConstraint(
            "review_evaluated_at >= evidence_as_of",
            name="ck_full_live_promotion_review_after_evidence",
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_full_live_promotion_top_n_positive"
        ),
        sa.ForeignKeyConstraint(
            ["canary_activation_id"], ["live_policy_canary_activations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["safety_binding_id"], ["live_policy_canary_safety_bindings.id"]
        ),
        sa.ForeignKeyConstraint(
            ["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["termination_event_id"], ["live_policy_canary_termination_events.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canary_activation_id", name="uq_full_live_promotion_canary_activation"
        ),
        sa.UniqueConstraint(
            "review_decision_signature", name="uq_full_live_promotion_review_decision"
        ),
    )
    op.create_index(
        "ix_full_live_promotion_candidate_approved",
        "full_live_policy_promotion_approvals",
        ["candidate_id", "human_approved_at"],
        unique=False,
    )
    op.create_index(
        "ix_full_live_promotion_context_approved",
        "full_live_policy_promotion_approvals",
        ["user_id", "exchange", "quote_asset", "human_approved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_full_live_promotion_context_approved",
        table_name="full_live_policy_promotion_approvals",
    )
    op.drop_index(
        "ix_full_live_promotion_candidate_approved",
        table_name="full_live_policy_promotion_approvals",
    )
    op.drop_table("full_live_policy_promotion_approvals")
