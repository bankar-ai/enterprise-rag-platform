"""Pydantic schemas for the generation API's request and response."""

import uuid

from pydantic import BaseModel, Field


class GenerationQuery(BaseModel):
    """A grounded-answer generation request.

    `conversation_id` is a stateless/stateful switch: omitted (`None`) keeps this request
    fully stateless -- no history loaded, nothing persisted, matching the original
    single-turn behavior exactly. Provided, it is a client-supplied UUID: if no
    conversation with that ID exists yet, one is created; if it does, it is continued.
    """

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = Field(default=False)
    expand_sections: bool = Field(default=False)
    conversation_id: uuid.UUID | None = Field(default=None)


class ConversationTurn(BaseModel):
    """One turn of conversation history, decoupled from how it's persisted."""

    role: str
    content: str


class Citation(BaseModel):
    """Provenance for one chunk that was included in the answer's context."""

    chunk_id: str
    document_id: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str


class GenerationResponse(BaseModel):
    """A synthesized answer with the citations backing its inline [n] markers.

    `conversation_id` is `None` for a stateless request, otherwise the conversation's ID
    (echoed back, or newly created on this call).
    """

    answer: str
    citations: list[Citation]
    conversation_id: uuid.UUID | None = None
