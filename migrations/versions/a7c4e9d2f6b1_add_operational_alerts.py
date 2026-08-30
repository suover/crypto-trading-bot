"""add operational alerts

Revision ID: a7c4e9d2f6b1
Revises: e6f2a9b4c7d1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a7c4e9d2f6b1"
down_revision: str | Sequence[str] | None = "e6f2a9b4c7d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alert_type", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("pipeline_run_id", sa.String(length=36), nullable=True),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=True),
        sa.Column("order_log_id", sa.BigInteger(), nullable=True),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=True),
        sa.Column("error_category", sa.String(length=30), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("safe_message", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "delivery_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "alert_type IN ('PIPELINE_FAILURE', 'STALE_LIVE_ORDER')",
            name="ck_operational_alerts_type",
        ),
        sa.CheckConstraint(
            "severity IN ('WARNING', 'CRITICAL')",
            name="ck_operational_alerts_severity",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('PENDING', 'SENT', 'FAILED')",
            name="ck_operational_alerts_delivery_status",
        ),
        sa.CheckConstraint(
            "delivery_attempt_count >= 0",
            name="ck_operational_alerts_attempt_count",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["order_log_id"], ["order_logs.id"]),
        sa.ForeignKeyConstraint(["recommendation_id"], ["trade_recommendations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_operational_alerts_dedup_key"),
    )
    for column_name in (
        "user_id",
        "pipeline_run_id",
        "analysis_run_id",
        "order_log_id",
        "recommendation_id",
    ):
        op.create_index(
            op.f(f"ix_operational_alerts_{column_name}"),
            "operational_alerts",
            [column_name],
            unique=False,
        )
    op.create_index(
        "ix_operational_alerts_delivery_due",
        "operational_alerts",
        ["delivery_status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_operational_alerts_type_resolved",
        "operational_alerts",
        ["alert_type", "resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operational_alerts_type_resolved", table_name="operational_alerts"
    )
    op.drop_index("ix_operational_alerts_delivery_due", table_name="operational_alerts")
    for column_name in reversed(
        (
            "user_id",
            "pipeline_run_id",
            "analysis_run_id",
            "order_log_id",
            "recommendation_id",
        )
    ):
        op.drop_index(
            op.f(f"ix_operational_alerts_{column_name}"),
            table_name="operational_alerts",
        )
    op.drop_table("operational_alerts")
