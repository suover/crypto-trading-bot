"""add limited live canary v1b safety and termination

Revision ID: e9a1b3c5d7f2
Revises: d8f0a2c4e6b1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e9a1b3c5d7f2"
down_revision: str | Sequence[str] | None = "d8f0a2c4e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_policy_canary_safety_bindings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("binding_schema_version", sa.String(60), nullable=False),
        sa.Column("canary_activation_id", sa.BigInteger(), nullable=False),
        sa.Column("activation_signature", sa.String(100), nullable=False),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("promotion_approval_signature", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("order_safety_policy_schema_version", sa.String(60), nullable=False),
        sa.Column(
            "order_safety_policy_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("order_safety_policy_signature", sa.String(110), nullable=False),
        sa.Column("max_buy_order_amount_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("daily_max_buy_amount_krw", sa.Numeric(30, 10), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_signature", sa.String(110), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_buy_order_amount_krw > 0",
            name="ck_live_canary_safety_binding_order_cap_positive",
        ),
        sa.CheckConstraint(
            "daily_max_buy_amount_krw > 0",
            name="ck_live_canary_safety_binding_daily_cap_positive",
        ),
        sa.CheckConstraint(
            "daily_max_buy_amount_krw >= max_buy_order_amount_krw",
            name="ck_live_canary_safety_binding_cap_order",
        ),
        sa.ForeignKeyConstraint(
            ["canary_activation_id"], ["live_policy_canary_activations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canary_activation_id", name="uq_live_canary_safety_binding_activation"
        ),
    )
    op.create_index(
        "ix_live_canary_safety_binding_context",
        "live_policy_canary_safety_bindings",
        ["user_id", "exchange", "quote_asset"],
        unique=False,
    )

    op.create_table(
        "live_policy_canary_termination_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("termination_schema_version", sa.String(60), nullable=False),
        sa.Column("canary_activation_id", sa.BigInteger(), nullable=False),
        sa.Column("activation_signature", sa.String(100), nullable=False),
        sa.Column("safety_binding_id", sa.BigInteger(), nullable=False),
        sa.Column("safety_binding_signature", sa.String(110), nullable=False),
        sa.Column("promotion_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("promotion_approval_signature", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("termination_source", sa.String(30), nullable=False),
        sa.Column("termination_reason", sa.String(30), nullable=False),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("termination_signature", sa.String(110), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "termination_source = 'MANUAL_CLI'",
            name="ck_live_canary_termination_source_manual",
        ),
        sa.CheckConstraint(
            "termination_reason = 'MANUAL_STOP'",
            name="ck_live_canary_termination_reason_manual_stop",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canary_activation_id", name="uq_live_canary_termination_activation"
        ),
    )
    op.create_index(
        "ix_live_canary_termination_context_time",
        "live_policy_canary_termination_events",
        ["user_id", "exchange", "quote_asset", "terminated_at"],
        unique=False,
    )

    op.drop_constraint(
        "ck_operational_alerts_type", "operational_alerts", type_="check"
    )
    op.create_check_constraint(
        "ck_operational_alerts_type",
        "operational_alerts",
        "alert_type IN ('PIPELINE_FAILURE', 'STALE_LIVE_ORDER', "
        "'LIVE_CANARY_STARTED', 'LIVE_CANARY_STOPPED', "
        "'LIVE_CANARY_BUY_LIMIT_BLOCKED', 'LIVE_CANARY_PROVENANCE_INVALID')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_operational_alerts_type", "operational_alerts", type_="check"
    )
    op.create_check_constraint(
        "ck_operational_alerts_type",
        "operational_alerts",
        "alert_type IN ('PIPELINE_FAILURE', 'STALE_LIVE_ORDER')",
    )
    op.drop_index(
        "ix_live_canary_termination_context_time",
        table_name="live_policy_canary_termination_events",
    )
    op.drop_table("live_policy_canary_termination_events")
    op.drop_index(
        "ix_live_canary_safety_binding_context",
        table_name="live_policy_canary_safety_bindings",
    )
    op.drop_table("live_policy_canary_safety_bindings")
