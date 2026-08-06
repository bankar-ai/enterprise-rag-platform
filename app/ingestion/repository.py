"""Persistence for ingested documents and their chunks."""

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
            document_id=chunk.document_id,
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
