"""Persistence for ingested documents and their chunks."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.models import ChunkRecord, DocumentRecord
from app.ingestion.schemas import Chunk


def save_document_and_chunks(
    session: Session, document_id: str, source_filename: str, chunks: list[Chunk]
) -> list[ChunkRecord]:
    """Persist one document and its chunks in `session`, flushing so `vector_id`s are assigned.

    Does not commit — the caller controls the transaction boundary.
    """
    session.add(DocumentRecord(document_id=document_id, filename=source_filename))
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


def get_chunks_by_vector_ids(session: Session, vector_ids: list[int]) -> dict[int, ChunkRecord]:
    """Fetch chunk rows by their `vector_id`s, keyed by `vector_id`. `{}` for empty input."""
    if not vector_ids:
        return {}
    rows = session.scalars(select(ChunkRecord).where(ChunkRecord.vector_id.in_(vector_ids))).all()
    return {row.vector_id: row for row in rows}


def search_chunks_by_text(session: Session, query_text: str, k: int) -> list[tuple[int, float]]:
    """Full-text search chunk text via Postgres, returning `(vector_id, rank)` pairs, best-first.

    `[]` for a blank query, `k <= 0`, or no matching chunks. Uses `plainto_tsquery` (safe against
    arbitrary user input, no `tsquery` syntax to escape) against the generated `search_vector`
    column, ranked by `ts_rank`.
    """
    if not query_text.strip() or k <= 0:
        return []
    tsquery = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank(ChunkRecord.search_vector, tsquery).label("rank")
    rows = session.execute(
        select(ChunkRecord.vector_id, rank)
        .where(ChunkRecord.search_vector.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(k)
    ).all()
    return [(int(vector_id), float(rank_value)) for vector_id, rank_value in rows]
