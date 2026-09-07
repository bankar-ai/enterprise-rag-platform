"""add oidc identities table and nullable hashed password

Revision ID: dd26f4e8c54f
Revises: f30732b09f7a
Create Date: 2026-09-06 21:45:47.707187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "dd26f4e8c54f"
down_revision: Union[str, Sequence[str], None] = "f30732b09f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # users.hashed_password becomes nullable: an OIDC-only user (see oidc_identities below)
    # never sets a local password. app.auth.service.login() explicitly checks for None before
    # verifying, so this can't be authenticated against via POST /auth/login.
    op.alter_column("users", "hashed_password", existing_type=sa.String(), nullable=True)

    op.create_table(
        "oidc_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_oidc_identities_provider_external_id"
        ),
    )
    op.create_index(op.f("ix_oidc_identities_user_id"), "oidc_identities", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Restoring `hashed_password` to NOT NULL assumes no OIDC-only user rows (NULL
    hashed_password) exist -- if any do, this alter_column fails, and any such rows must be
    deleted or given a placeholder hash first (mirrors this project's other migrations, which
    don't attempt to be safe against every possible post-forward-migration data state).
    """
    op.drop_index(op.f("ix_oidc_identities_user_id"), table_name="oidc_identities")
    op.drop_table("oidc_identities")
    op.alter_column("users", "hashed_password", existing_type=sa.String(), nullable=False)
