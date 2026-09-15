"""remove retired canary promotion flow

Revision ID: a1b2c3d4e5f6
Revises: f0c2e4a6b8d1
"""

from collections.abc import Sequence
import importlib

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f0c2e4a6b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _migration(name: str):
    return importlib.import_module(f"migrations.versions.{name}")


def upgrade() -> None:
    op.drop_index(
        "ix_full_live_promotion_context_approved",
        table_name="full_live_policy_promotion_approvals",
    )
    op.drop_index(
        "ix_full_live_promotion_candidate_approved",
        table_name="full_live_policy_promotion_approvals",
    )
    op.drop_table("full_live_policy_promotion_approvals")

    # Retired alert rows cannot satisfy the restored non-Canary type constraint.
    op.execute(
        "DELETE FROM operational_alerts "
        "WHERE alert_type IN "
        "('LIVE_CANARY_STARTED', 'LIVE_CANARY_STOPPED', "
        "'LIVE_CANARY_BUY_LIMIT_BLOCKED', 'LIVE_CANARY_PROVENANCE_INVALID')"
    )
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
    op.drop_index(
        "ix_live_canary_run_context_reserved",
        table_name="live_policy_canary_runs",
    )
    op.drop_index(
        "ix_live_canary_run_activation_reserved",
        table_name="live_policy_canary_runs",
    )
    op.drop_table("live_policy_canary_runs")
    op.drop_index(
        "ix_live_canary_activation_context_started",
        table_name="live_policy_canary_activations",
    )
    op.drop_table("live_policy_canary_activations")


def downgrade() -> None:
    # Schema-only recreation. Rows removed by upgrade are intentionally not
    # reconstructed.
    _migration("d8f0a2c4e6b1_add_limited_live_canary").upgrade()
    _migration("e9a1b3c5d7f2_add_limited_live_canary_v1b").upgrade()
    _migration("f0c2e4a6b8d1_add_full_live_promotion_approvals").upgrade()
