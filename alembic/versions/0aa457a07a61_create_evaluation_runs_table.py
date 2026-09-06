"""create evaluation_runs table

Revision ID: 0aa457a07a61
Revises: cc12bb2f6bc7
Create Date: 2026-09-06 10:38:40.090931

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0aa457a07a61'
down_revision: Union[str, Sequence[str], None] = 'cc12bb2f6bc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("num_queries", sa.Integer(), nullable=False),
        sa.Column("mean_precision_at_k", sa.Float(), nullable=False),
        sa.Column("mean_recall_at_k", sa.Float(), nullable=False),
        sa.Column("mrr", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("evaluation_runs")
