"""One-off migration: redistribute a legacy shared FAISS index into per-owner indexes.

Run via: `uv run python -m app.embedding.migrate_to_per_owner [--legacy-index-path PATH]
[--index-dir DIR]`.

Context (ERP-031): before this ticket, every ingested chunk's vector lived in one shared
FAISS index file (`app/embedding/config.py`'s old `faiss_index_path`, default
`data/faiss_index.bin`). Isolation between owners was enforced by filtering search results
after the fact, not by the index itself. ERP-031 replaces this with one physically separate
FAISS index per owner (`app.embedding.index.OwnerFaissIndexStore`); this script migrates any
data ingested before that change.

This is deliberately not an Alembic migration: Alembic tracks Postgres *schema*, and this
step changes neither `ChunkRecord.vector_id` nor `DocumentRecord.owner_id` -- it only
redistributes FAISS's *data* files, which Alembic has no visibility into at all.

Approach: read every `(vector_id, owner_id)` pair from Postgres (`chunks` joined to
`documents`), reconstruct each vector from the legacy index by its `vector_id` (safe because
the legacy index is a flat, non-quantized index -- see `FaissIndex.reconstruct`), and add it
to the new per-owner store for that owner. A chunk row with no corresponding vector in the
legacy index (e.g. from the non-atomic Postgres/FAISS writes ERP-011 already documents) is
skipped with a logged warning rather than aborting the whole run -- it was never searchable
before this migration either, so skipping it doesn't regress anything. Read-only against
Postgres and the legacy index; never deletes or modifies either. Safe to re-run: it always
rebuilds full per-owner indexes from Postgres + the legacy index rather than incrementally
patching, so a partial or failed run can simply be re-run from scratch.

The legacy index file itself is intentionally NOT deleted by this script -- once the new
per-owner indexes are confirmed correct (e.g. by running the retrieval evaluation harness,
or spot-checking `POST /retrieval/query`), remove it manually.
"""

import argparse
import logging
import os
import uuid
from collections import defaultdict

from sqlalchemy import select

from app.core.db import get_session_factory
from app.embedding.config import get_embedding_settings
from app.embedding.index import FaissIndex, OwnerFaissIndexStore
from app.ingestion.models import ChunkRecord, DocumentRecord

logger = logging.getLogger(__name__)


def migrate(legacy_index_path: str, index_dir: str) -> dict[str, int]:
    """Redistribute `legacy_index_path`'s vectors into `index_dir`, one index file per owner.

    Returns a `{str(owner_id): vectors_migrated}` summary. `{}` if there are no chunks at
    all (nothing to do).
    """
    settings = get_embedding_settings()
    legacy_index = FaissIndex(legacy_index_path, settings.dimension)
    store = OwnerFaissIndexStore(index_dir, settings.dimension)

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(
            select(ChunkRecord.vector_id, DocumentRecord.owner_id).join(
                DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id
            )
        ).all()

    vector_ids_by_owner: dict[uuid.UUID, list[int]] = defaultdict(list)
    for vector_id, owner_id in rows:
        vector_ids_by_owner[owner_id].append(vector_id)

    summary: dict[str, int] = {}
    for owner_id, vector_ids in vector_ids_by_owner.items():
        # Rebuild this owner's destination index from scratch rather than appending, so
        # re-running the script after a partial/failed run never duplicates vectors.
        destination_path = store.path_for(owner_id)
        if os.path.exists(destination_path):
            os.remove(destination_path)

        found_vector_ids: list[int] = []
        found_vectors: list[list[float]] = []
        for vector_id in vector_ids:
            try:
                found_vectors.append(legacy_index.reconstruct(vector_id))
            except RuntimeError:
                # A chunk row with no corresponding vector in the legacy index -- e.g. from
                # the non-atomic Postgres/FAISS writes ERP-011 already documents. Skip it
                # rather than aborting the whole migration; it was never searchable before
                # this migration either, so this doesn't regress anything.
                logger.warning(
                    "vector_id=%s (owner=%s) has no vector in the legacy index -- skipping",
                    vector_id,
                    owner_id,
                )
                continue
            found_vector_ids.append(vector_id)

        store.add(owner_id, found_vector_ids, found_vectors)
        summary[str(owner_id)] = len(found_vector_ids)
        logger.info("Migrated %d vector(s) for owner %s", len(found_vector_ids), owner_id)

    return summary


def main() -> None:
    """Parse CLI args and run the migration, printing a summary report.

    This module's stdout report output is a deliberate exception to the no-`print()` rule
    (same rationale as `app.evaluation.run`'s CLI entrypoint) -- not a violation to fix.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-index-path",
        default="data/faiss_index.bin",
        help="Path to the pre-ERP-031 shared FAISS index file (default: data/faiss_index.bin).",
    )
    parser.add_argument(
        "--index-dir",
        default=None,
        help="Destination directory for per-owner index files "
        "(default: the app's configured EMBEDDING_FAISS_INDEX_DIR).",
    )
    args = parser.parse_args()

    index_dir = args.index_dir or get_embedding_settings().faiss_index_dir
    summary = migrate(args.legacy_index_path, index_dir)

    print(f"Migrated {len(summary)} owner(s) from {args.legacy_index_path!r} into {index_dir!r}:")
    for owner_id, count in summary.items():
        print(f"  {owner_id}: {count} vector(s)")
    if not summary:
        print("  (no chunks found -- nothing to migrate)")
    print(f"\nThe legacy index at {args.legacy_index_path!r} was not modified or deleted.")
    print("Once the new per-owner indexes are confirmed correct, remove it manually.")


if __name__ == "__main__":
    main()
