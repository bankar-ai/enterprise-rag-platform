"""convert search_vector to trigger-based non-locking migration

Revision ID: ec9863a88014
Revises: cafac1f26e4f
Create Date: 2026-09-05 01:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR


# revision identifiers, used by Alembic.
revision: str = 'ec9863a88014'
down_revision: Union[str, Sequence[str], None] = 'cafac1f26e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRIGGER_FUNCTION_SQL = """
    CREATE OR REPLACE FUNCTION chunks_search_vector_update() RETURNS trigger AS $$
    BEGIN
        NEW.search_vector := to_tsvector('english', NEW.text);
        RETURN NEW;
    END
    $$ LANGUAGE plpgsql;
"""

_TRIGGER_SQL = """
    CREATE TRIGGER chunks_search_vector_trigger
    BEFORE INSERT OR UPDATE OF text ON chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_search_vector_update();
"""

_BACKFILL_SQL = """
    DO $$
    DECLARE
        rows_updated integer;
    BEGIN
        LOOP
            UPDATE chunks
            SET search_vector = to_tsvector('english', text)
            WHERE chunk_id IN (
                SELECT chunk_id FROM chunks WHERE search_vector IS NULL LIMIT 1000
            );
            GET DIAGNOSTICS rows_updated = ROW_COUNT;
            EXIT WHEN rows_updated = 0;
        END LOOP;
    END $$;
"""


def upgrade() -> None:
    """Upgrade schema.

    Replaces the `GENERATED ALWAYS`/non-concurrent-index approach from a97a8780506f with a
    non-locking pattern: a plain column kept in sync by a trigger, existing rows backfilled in
    batches, and the index rebuilt CONCURRENTLY. Everything up to the index rebuild runs inside
    one transaction, so no other session ever observes an intermediate (trigger-less) state.
    """
    op.drop_index('ix_chunks_search_vector', table_name='chunks')

    # Converts the column to plain/non-generated in place; no table rewrite, existing computed
    # values are kept as static data.
    op.execute("ALTER TABLE chunks ALTER COLUMN search_vector DROP EXPRESSION")

    op.execute(_TRIGGER_FUNCTION_SQL)
    op.execute(_TRIGGER_SQL)

    # No-op today (every row already has a value from the dropped generated expression) but
    # keeps this migration correct as a general pattern if applied to a table with existing
    # untriggered rows.
    op.execute(_BACKFILL_SQL)

    # CONCURRENTLY cannot run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_chunks_search_vector ON chunks USING gin (search_vector)"
        )


def downgrade() -> None:
    """Downgrade schema. Restores a97a8780506f's generated column + non-concurrent index."""
    op.execute("DROP INDEX IF EXISTS ix_chunks_search_vector")
    op.execute("DROP TRIGGER IF EXISTS chunks_search_vector_trigger ON chunks")
    op.execute("DROP FUNCTION IF EXISTS chunks_search_vector_update()")
    op.drop_column('chunks', 'search_vector')
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
