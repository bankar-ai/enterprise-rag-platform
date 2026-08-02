"""The ingestion service: orchestrates parsing and chunking a PDF into `Chunk`s."""

import uuid

from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import parse_pdf
from app.ingestion.schemas import Chunk, IngestResponse


def ingest_pdf(pdf_path: str, source_filename: str, settings: IngestionSettings) -> IngestResponse:
    """Parse and chunk a PDF at `pdf_path`, returning provenance-tagged chunks."""
    document_id = str(uuid.uuid4())
    pages, parser_used = parse_pdf(pdf_path, settings)
    raw_chunks = chunk_markdown(pages, settings)

    chunks = [
        Chunk(
            chunk_id=f"{document_id}-{index}",
            document_id=document_id,
            chunk_index=index,
            text=raw["text"],
            section_path=raw["section_path"],
            page_start=raw["page_start"],
            page_end=raw["page_end"],
            char_count=raw["char_count"],
            parser_used=parser_used,
            source_filename=source_filename,
        )
        for index, raw in enumerate(raw_chunks)
    ]

    return IngestResponse(document_id=document_id, chunks=chunks)
