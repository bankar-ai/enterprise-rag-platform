"""Persistence for ingested documents and their chunks."""

import uuid
from collections.abc import Set as AbstractSet

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.models import ChunkRecord, DocumentRecord
from app.ingestion.schemas import Chunk


def save_document_and_chunks(
    session: Session,
    document_id: str,
    source_filename: str,
    chunks: list[Chunk],
    owner_id: uuid.UUID,
) -> list[ChunkRecord]:
    """Persist one document and its chunks in `session`, flushing so `vector_id`s are assigned.

    Does not commit — the caller controls the transaction boundary.
    """
    session.add(DocumentRecord(document_id=document_id, filename=source_filename, owner_id=owner_id))
    session.flush()

    records = [
        ChunkRecord(
            chunk_id=chunk.chunk_id,
            document_id=document_id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            char_count=chunk.char_count,
            parser_used=chunk.parser_used,
            source_filename=chunk.source_filename,
        )
        for chunk in chunks
    ]
    session.add_all(records)
    session.flush()
    return records


def get_chunks_by_vector_ids(
    session: Session, vector_ids: list[int], owner_id: uuid.UUID
) -> dict[int, ChunkRecord]:
    """Fetch chunk rows by their `vector_id`s, restricted to `owner_id`'s documents.

    Keyed by `vector_id`. `{}` for empty input.
    """
    if not vector_ids:
        return {}
    rows = session.scalars(
        select(ChunkRecord)
        .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
        .where(ChunkRecord.vector_id.in_(vector_ids), DocumentRecord.owner_id == owner_id)
    ).all()
    return {row.vector_id: row for row in rows}


def filter_vector_ids_by_owner(
    session: Session, vector_ids: list[int], owner_id: uuid.UUID
) -> list[int]:
    """Return the subset of `vector_ids` whose chunk belongs to a document owned by `owner_id`.

    Used to restrict FAISS search hits (which carry no owner information of their own) to
    `owner_id`'s documents *before* rank fusion truncates to `top_k`, so another owner's
    vector hits can't consume a caller's result slots. Order is not preserved -- callers that
    need best-first order should filter their original list against the returned set rather
    than use this list directly.
    """
    if not vector_ids:
        return []
    rows = session.scalars(
        select(ChunkRecord.vector_id)
        .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
        .where(ChunkRecord.vector_id.in_(vector_ids), DocumentRecord.owner_id == owner_id)
    ).all()
    return list(rows)


def search_chunks_by_text(
    session: Session, query_text: str, k: int, owner_id: uuid.UUID
) -> list[tuple[int, float]]:
    """Full-text search chunk text via Postgres, restricted to `owner_id`'s documents.

    Returns `(vector_id, rank)` pairs, best-first. `[]` for a blank query, `k <= 0`, or no
    matching chunks. Uses `plainto_tsquery` (safe against arbitrary user input, no `tsquery`
    syntax to escape) against the generated `search_vector` column, ranked by `ts_rank`.
    """
    if not query_text.strip() or k <= 0:
        return []
    tsquery = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank(ChunkRecord.search_vector, tsquery).label("rank")
    rows = session.execute(
        select(ChunkRecord.vector_id, rank)
        .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
        .where(ChunkRecord.search_vector.op("@@")(tsquery), DocumentRecord.owner_id == owner_id)
        .order_by(rank.desc())
        .limit(k)
    ).all()
    return [(int(vector_id), float(rank_value)) for vector_id, rank_value in rows]


def get_sibling_chunks(
    session: Session,
    document_id: str,
    section_path: list[str],
    exclude_chunk_ids: AbstractSet[str] = frozenset(),
) -> list[ChunkRecord]:
    """Return `document_id`'s chunks whose `section_path` exactly equals `section_path`.

    Ordered by `chunk_index`; excludes any `chunk_id` in `exclude_chunk_ids`. Filters by
    section in Python (not SQL) because `chunks.section_path` is a Postgres `json` column,
    which has no `=` operator (see `.ai/adr/ADR-007.md`) -- comparison happens against the
    already-indexed `document_id`'s (typically small) chunk set.
    """
    rows = session.scalars(
        select(ChunkRecord).where(ChunkRecord.document_id == document_id).order_by(ChunkRecord.chunk_index)
    ).all()
    return [
        row
        for row in rows
        if row.section_path == section_path and row.chunk_id not in exclude_chunk_ids
    ]
