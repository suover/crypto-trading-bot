"""add portfolio valuation policy identity

Revision ID: b8c2d4e6f1a3
Revises: f1b7c3d9e5a2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b8c2d4e6f1a3"
down_revision: str | Sequence[str] | None = "f1b7c3d9e5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "portfolio_snapshots",
        sa.Column("valuation_policy_signature", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_snapshots", "valuation_policy_signature")
