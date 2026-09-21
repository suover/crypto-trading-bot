"""add research policy candidate registry

Revision ID: f4a8c2e6b1d3
Revises: e2f4a6b8c0d1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4a8c2e6b1d3"
down_revision: str | Sequence[str] | None = "e2f4a6b8c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_policy_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("candidate_schema_version", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("quote_asset", sa.String(length=20), nullable=False),
        sa.Column("scenario_name", sa.String(length=80), nullable=False),
        sa.Column(
            "scenario_definition_signature", sa.String(length=100), nullable=False
        ),
        sa.Column(
            "component_weights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reference_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "reference_snapshot_captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("dataset_schema_version", sa.String(length=50), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(length=100), nullable=False),
        sa.Column("effective_top_n", sa.Integer(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "registration_snapshot_id_watermark", sa.BigInteger(), nullable=False
        ),
        sa.Column(
            "registration_captured_at_watermark",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_top_n > 0", name="ck_rpc_effective_top_n_positive"
        ),
        sa.CheckConstraint(
            "registration_snapshot_id_watermark > 0",
            name="ck_rpc_snapshot_watermark_positive",
        ),
        sa.ForeignKeyConstraint(
            ["reference_snapshot_id"], ["strategy_replay_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "exchange",
            "quote_asset",
            "baseline_policy_signature",
            "effective_top_n",
            "scenario_definition_signature",
            name="uq_rpc_context_definition_signature",
        ),
        sa.UniqueConstraint(
            "user_id",
            "exchange",
            "quote_asset",
            "baseline_policy_signature",
            "effective_top_n",
            "scenario_name",
            name="uq_rpc_context_scenario_name",
        ),
    )
    op.create_index(
        "ix_research_policy_candidates_reference_snapshot_id",
        "research_policy_candidates",
        ["reference_snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_research_policy_candidates_user_id",
        "research_policy_candidates",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_rpc_context_registered_at",
        "research_policy_candidates",
        ["user_id", "exchange", "quote_asset", "registered_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rpc_context_registered_at", table_name="research_policy_candidates"
    )
    op.drop_index(
        "ix_research_policy_candidates_user_id",
        table_name="research_policy_candidates",
    )
    op.drop_index(
        "ix_research_policy_candidates_reference_snapshot_id",
        table_name="research_policy_candidates",
    )
    op.drop_table("research_policy_candidates")
