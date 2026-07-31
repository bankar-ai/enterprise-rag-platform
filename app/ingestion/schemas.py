from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Chunk(BaseModel):
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
    document_id: str
    chunks: list[Chunk]


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    status: JobStatus
    result: IngestResponse | None = None
    error: str | None = None
