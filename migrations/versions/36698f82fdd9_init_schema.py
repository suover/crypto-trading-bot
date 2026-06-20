"""init schema

Revision ID: 36698f82fdd9
Revises: 
Create Date: 2026-06-20 15:36:06.647345

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = '36698f82fdd9'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
