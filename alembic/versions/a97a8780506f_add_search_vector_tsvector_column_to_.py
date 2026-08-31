"""add search_vector tsvector column to chunks

Revision ID: a97a8780506f
Revises: 544c6d2a4004
Create Date: 2026-08-26 09:07:20.916590

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR


# revision identifiers, used by Alembic.
revision: str = 'a97a8780506f'
down_revision: Union[str, Sequence[str], None] = '544c6d2a4004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'chunks',
        sa.Column(
            'search_vector',
            TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_chunks_search_vector', 'chunks', ['search_vector'], unique=False, postgresql_using='gin'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chunks_search_vector', table_name='chunks')
    op.drop_column('chunks', 'search_vector')
