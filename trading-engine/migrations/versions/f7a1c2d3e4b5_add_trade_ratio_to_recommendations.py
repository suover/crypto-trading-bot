"""add trade ratio to recommendations

Revision ID: f7a1c2d3e4b5
Revises: c5e7a9b1d3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f7a1c2d3e4b5"
down_revision: str | Sequence[str] | None = "c5e7a9b1d3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trade_recommendations",
        sa.Column("trade_ratio", sa.Numeric(precision=10, scale=9), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_recommendations", "trade_ratio")
