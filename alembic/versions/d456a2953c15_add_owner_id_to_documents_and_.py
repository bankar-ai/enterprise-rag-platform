"""add owner_id to documents and conversations

Revision ID: d456a2953c15
Revises: ed6433ffe0e7
Create Date: 2026-09-05 11:16:39.617415

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d456a2953c15"
down_revision: Union[str, Sequence[str], None] = "ed6433ffe0e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"
_SYSTEM_USER_EMAIL = "system@internal"
_SYSTEM_USER_HASH = "!"  # not a valid Argon2 hash -- this account can never log in


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("conversations", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO users (id, email, hashed_password, role) "
            "VALUES (:id, :email, :hashed_password, 'admin') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": _SYSTEM_USER_ID, "email": _SYSTEM_USER_EMAIL, "hashed_password": _SYSTEM_USER_HASH},
    )
    connection.execute(
        sa.text("UPDATE documents SET owner_id = :id WHERE owner_id IS NULL"),
        {"id": _SYSTEM_USER_ID},
    )
    connection.execute(
        sa.text("UPDATE conversations SET owner_id = :id WHERE owner_id IS NULL"),
        {"id": _SYSTEM_USER_ID},
    )

    op.alter_column("documents", "owner_id", nullable=False)
    op.alter_column("conversations", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_owner_id_users", "documents", "users", ["owner_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_conversations_owner_id_users", "conversations", "users", ["owner_id"], ["id"]
    )
    op.create_index(op.f("ix_documents_owner_id"), "documents", ["owner_id"])
    op.create_index(op.f("ix_conversations_owner_id"), "conversations", ["owner_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_conversations_owner_id"), table_name="conversations")
    op.drop_index(op.f("ix_documents_owner_id"), table_name="documents")
    op.drop_constraint("fk_conversations_owner_id_users", "conversations", type_="foreignkey")
    op.drop_constraint("fk_documents_owner_id_users", "documents", type_="foreignkey")
    op.drop_column("conversations", "owner_id")
    op.drop_column("documents", "owner_id")
