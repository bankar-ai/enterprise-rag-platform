"""Pydantic schemas for the generation API's request and response."""

from pydantic import BaseModel, Field


class GenerationQuery(BaseModel):
    """A grounded-answer generation request."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = Field(default=False)
    expand_sections: bool = Field(default=False)


class Citation(BaseModel):
    """Provenance for one chunk that was included in the answer's context."""

    chunk_id: str
    document_id: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str


class GenerationResponse(BaseModel):
    """A synthesized answer with the citations backing its inline [n] markers."""

    answer: str
    citations: list[Citation]
