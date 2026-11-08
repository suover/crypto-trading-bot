"""Persist the Research Candidate Automation checkpoint per replay context."""

from alembic import op
import sqlalchemy as sa


revision = "c4e6a8b0d2f1"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_candidate_automation_states",
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("users.id"), primary_key=True
        ),
        sa.Column("exchange", sa.String(30), primary_key=True),
        sa.Column("quote_asset", sa.String(20), primary_key=True),
        sa.Column("dataset_schema_version", sa.String(50), primary_key=True),
        sa.Column("baseline_policy_signature", sa.String(100), primary_key=True),
        sa.Column("effective_top_n", sa.Integer(), primary_key=True),
        sa.Column("last_observed_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_status", sa.String(50), nullable=False),
        sa.Column("review_cycle_cap", sa.Integer(), nullable=False),
        sa.Column("review_context_cap", sa.Integer(), nullable=False),
        sa.Column("last_reference_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("last_status", sa.String(50), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_registration_status", sa.String(50), nullable=True),
        sa.Column("last_registered_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("last_registered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("research_candidate_automation_states")
