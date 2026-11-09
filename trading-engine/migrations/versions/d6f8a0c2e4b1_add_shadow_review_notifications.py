"""Persist Shadow Review monitoring progress and notification delivery."""

from alembic import op
import sqlalchemy as sa

revision = "d6f8a0c2e4b1"
down_revision = "c4e6a8b0d2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_review_monitor_states",
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("exchange", sa.String(30), primary_key=True, nullable=False),
        sa.Column("quote_asset", sa.String(20), primary_key=True, nullable=False),
        sa.Column("cursor_enrollment_id", sa.BigInteger, nullable=False),
        sa.Column("sweep_enrollment_id_ceiling", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "shadow_review_notifications",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False
        ),
        sa.Column(
            "candidate_id",
            sa.BigInteger,
            sa.ForeignKey("research_policy_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "shadow_enrollment_id",
            sa.BigInteger,
            sa.ForeignKey("shadow_policy_enrollments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("scenario_definition_signature", sa.String(100), nullable=False),
        sa.Column("baseline_policy_signature", sa.String(100), nullable=False),
        sa.Column("effective_top_n", sa.Integer, nullable=False),
        sa.Column("review_policy_schema_version", sa.String(50), nullable=False),
        sa.Column("review_policy_signature", sa.String(100), nullable=False),
        sa.Column("initial_review_decision_signature", sa.String(100), nullable=False),
        sa.Column("first_eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "shadow_evaluation_snapshot_id_ceiling", sa.BigInteger, nullable=False
        ),
        sa.Column("notification_destination", sa.String(32), nullable=False),
        sa.Column("delivery_status", sa.String(16), nullable=False),
        sa.Column(
            "delivery_attempt_count", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_failure_code", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "shadow_enrollment_id",
            "review_policy_signature",
            "notification_destination",
            name="uq_shadow_review_notification_destination",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'SUPPRESSED', 'UNCERTAIN')",
            name="ck_shadow_review_notification_status",
        ),
        sa.CheckConstraint(
            "delivery_attempt_count >= 0", name="ck_shadow_review_notification_attempts"
        ),
        sa.CheckConstraint(
            "delivery_status <> 'SENDING' OR claimed_at IS NOT NULL",
            name="ck_shadow_review_notification_claim",
        ),
        sa.CheckConstraint(
            "delivery_status <> 'SENT' OR (telegram_message_id IS NOT NULL AND telegram_message_id > 0 AND sent_at IS NOT NULL)",
            name="ck_shadow_review_notification_sent",
        ),
        sa.CheckConstraint(
            "effective_top_n > 0 AND shadow_evaluation_snapshot_id_ceiling > 0",
            name="ck_shadow_review_notification_evidence",
        ),
    )
    op.create_index(
        "ix_shadow_review_notification_due",
        "shadow_review_notifications",
        [
            "user_id",
            "exchange",
            "quote_asset",
            "notification_destination",
            "delivery_status",
            "next_retry_at",
            "id",
        ],
    )
    op.create_index(
        "ix_shadow_review_notification_candidate",
        "shadow_review_notifications",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_table("shadow_review_notifications")
    op.drop_table("shadow_review_monitor_states")
