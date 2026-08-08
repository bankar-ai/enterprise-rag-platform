"""Pydantic schemas for the retrieval API's request and response."""

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    """A semantic search request."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class RetrievedChunk(BaseModel):
    """A single retrieved chunk, with full provenance metadata and a similarity score."""

    chunk_id: str
    document_id: str
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str
    score: float


class RetrievalResponse(BaseModel):
    """Ranked results of a semantic search query, most relevant first."""

    results: list[RetrievedChunk]
