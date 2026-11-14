"""Rename the authentication accounts table in place."""

from alembic import op


revision = "c2f84b7a931e"
down_revision = "ab9f4e701c32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("dashboard_accounts", "accounts", schema="auth")


def downgrade() -> None:
    op.rename_table("accounts", "dashboard_accounts", schema="auth")
