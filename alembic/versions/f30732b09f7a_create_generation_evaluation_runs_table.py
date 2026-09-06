"""create generation_evaluation_runs table

Revision ID: f30732b09f7a
Revises: 0aa457a07a61
Create Date: 2026-09-06 15:11:52.377807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f30732b09f7a'
down_revision: Union[str, Sequence[str], None] = '0aa457a07a61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generation_evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("judge", sa.String(), nullable=False),
        sa.Column("num_queries", sa.Integer(), nullable=False),
        sa.Column("mean_faithfulness", sa.Float(), nullable=False),
        sa.Column("mean_answer_relevancy", sa.Float(), nullable=False),
        sa.Column("mean_context_precision", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("generation_evaluation_runs")
