"""Pydantic schemas for ingestion API requests, responses, and job status."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Chunk(BaseModel):
    """A single chunk of parsed document text, with full provenance metadata."""

    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    char_count: int
    parser_used: Literal["fast", "quality"]
    source_filename: str


class IngestResponse(BaseModel):
    """The completed result of ingesting one document: its ID and resulting chunks."""

    document_id: str
    chunks: list[Chunk]


class JobStatus(str, Enum):
    """Lifecycle status of an async ingestion job."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    """Polled status of an ingestion job, with its result or error once finished."""

    status: JobStatus
    result: IngestResponse | None = None
    error: str | None = None
