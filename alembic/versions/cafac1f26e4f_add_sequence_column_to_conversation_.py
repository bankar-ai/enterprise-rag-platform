"""add sequence column to conversation messages

Revision ID: cafac1f26e4f
Revises: b4e7f3af8d65
Create Date: 2026-09-01 09:39:43.214132

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cafac1f26e4f'
down_revision: Union[str, Sequence[str], None] = 'b4e7f3af8d65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversation_messages',
        sa.Column('sequence', sa.Integer(), sa.Identity(always=True), nullable=False),
    )
    op.create_unique_constraint(
        'uq_conversation_messages_sequence', 'conversation_messages', ['sequence']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_conversation_messages_sequence', 'conversation_messages', type_='unique')
    op.drop_column('conversation_messages', 'sequence')
