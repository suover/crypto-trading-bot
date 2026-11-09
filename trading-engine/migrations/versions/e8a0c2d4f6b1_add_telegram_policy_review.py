"""Track Telegram policy review, and extend approval source with bound audit."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e8a0c2d4f6b1"
down_revision = "d6f8a0c2e4b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    approval = "shadow_policy_promotion_approvals"
    op.add_column(
        approval,
        sa.Column("telegram_review_context", postgresql.JSONB(), nullable=True),
    )
    op.drop_constraint("ck_shadow_promotion_source_manual", approval, type_="check")
    op.create_check_constraint(
        "ck_shadow_promotion_source",
        approval,
        "(approval_source = 'MANUAL_CLI' AND telegram_review_context IS NULL) OR (approval_source = 'TELEGRAM' AND telegram_review_context IS NOT NULL AND jsonb_typeof(telegram_review_context) = 'object')",
    )
    op.create_table(
        "telegram_policy_review_requests",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False
        ),
        sa.Column(
            "notification_id",
            sa.BigInteger,
            sa.ForeignKey("shadow_review_notifications.id"),
            nullable=False,
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
            sa.ForeignKey("shadow_policy_enrollments.id"),
            nullable=False,
        ),
        sa.Column("review_policy_signature", sa.String(100), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger, nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger, nullable=True),
        sa.Column("authorized_telegram_user_id", sa.BigInteger, nullable=True),
        sa.Column("review_token_hash", sa.String(64), nullable=False),
        sa.Column("confirmation_token_hash", sa.String(64), nullable=True),
        sa.Column("evidence_fingerprint", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("review_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_id",
            sa.BigInteger,
            sa.ForeignKey("shadow_policy_promotion_approvals.id"),
            nullable=True,
        ),
        sa.Column("approval_signature", sa.String(100), nullable=True),
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
        sa.UniqueConstraint("notification_id", name="uq_tg_policy_review_notification"),
        sa.UniqueConstraint("review_token_hash", name="uq_tg_policy_review_token"),
        sa.UniqueConstraint(
            "confirmation_token_hash", name="uq_tg_policy_confirmation_token"
        ),
        sa.UniqueConstraint("approval_id", name="uq_tg_policy_review_approval"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'REVIEWED', 'APPROVED', 'REJECTED', 'EXPIRED', 'STALE', 'CANCELLED')",
            name="ck_tg_policy_review_status",
        ),
        sa.CheckConstraint(
            "(telegram_message_id IS NULL) = (review_expires_at IS NULL)",
            name="ck_tg_policy_review_receipt",
        ),
        sa.CheckConstraint(
            "(confirmation_token_hash IS NULL) = (confirmation_expires_at IS NULL)",
            name="ck_tg_policy_review_confirmation",
        ),
        sa.CheckConstraint(
            "status NOT IN ('REVIEWED', 'APPROVED', 'REJECTED') OR (telegram_message_id > 0 AND telegram_message_id IS NOT NULL AND authorized_telegram_user_id > 0 AND authorized_telegram_user_id IS NOT NULL AND evidence_fingerprint IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_tg_policy_review_evidence",
        ),
        sa.CheckConstraint(
            "status NOT IN ('APPROVED', 'REJECTED') OR (decided_at IS NOT NULL AND confirmation_token_hash IS NOT NULL)",
            name="ck_tg_policy_review_decision",
        ),
        sa.CheckConstraint(
            "(status = 'APPROVED' AND approval_id IS NOT NULL AND approval_signature IS NOT NULL) OR (status <> 'APPROVED' AND approval_id IS NULL AND approval_signature IS NULL)",
            name="ck_tg_policy_review_approval",
        ),
    )
    op.create_index(
        "ix_tg_policy_review_expiry",
        "telegram_policy_review_requests",
        ["status", "review_expires_at", "confirmation_expires_at"],
    )
    op.create_index(
        "ix_tg_policy_review_candidate",
        "telegram_policy_review_requests",
        ["candidate_id", "shadow_enrollment_id"],
    )


def downgrade() -> None:
    approval = "shadow_policy_promotion_approvals"
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM shadow_policy_promotion_approvals WHERE approval_source = 'TELEGRAM' LIMIT 1"
            )
        )
        .scalar()
        is not None
    ):
        raise RuntimeError(
            "Cannot downgrade while Telegram approval audit records exist"
        )
    op.drop_table("telegram_policy_review_requests")
    op.drop_constraint("ck_shadow_promotion_source", approval, type_="check")
    op.drop_column(approval, "telegram_review_context")
    op.create_check_constraint(
        "ck_shadow_promotion_source_manual", approval, "approval_source = 'MANUAL_CLI'"
    )
