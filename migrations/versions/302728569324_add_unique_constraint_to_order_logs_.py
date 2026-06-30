"""add unique constraint to order logs recommendation

Revision ID: 302728569324
Revises: 2766db72643b
Create Date: 2026-06-23 20:31:47.679534

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "302728569324"
down_revision: Union[str, Sequence[str], None] = "2766db72643b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(
        op.f("ix_order_logs_recommendation_id"),
        table_name="order_logs",
    )

    op.create_unique_constraint(
        "uq_order_logs_recommendation_id",
        "order_logs",
        ["recommendation_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_order_logs_recommendation_id",
        "order_logs",
        type_="unique",
    )

    op.create_index(
        op.f("ix_order_logs_recommendation_id"),
        "order_logs",
        ["recommendation_id"],
        unique=False,
    )
