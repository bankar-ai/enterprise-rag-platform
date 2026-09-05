"""add is_active to users

Revision ID: cc12bb2f6bc7
Revises: d456a2953c15
Create Date: 2026-09-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cc12bb2f6bc7"
down_revision: Union[str, Sequence[str], None] = "d456a2953c15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "is_active")
