"""Generation API: grounded answer synthesis over retrieved chunks."""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.generation.schemas import ConversationHistoryResponse, GenerationQuery, GenerationResponse
from app.generation.service import generate, generate_stream, get_conversation_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/query")
def query(query_request: GenerationQuery) -> GenerationResponse:
    """Run retrieval + LLM synthesis and return a grounded, cited answer."""
    try:
        return generate(
            query_request.query,
            query_request.top_k,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
            conversation_id=query_request.conversation_id,
        )
    except Exception as exc:
        logger.exception("Generation query failed")
        raise HTTPException(status_code=503, detail="Generation query failed") from exc


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _event_stream(query_request: GenerationQuery) -> Iterator[str]:
    for event, data in generate_stream(
        query_request.query,
        query_request.top_k,
        rerank=query_request.rerank,
        expand_sections=query_request.expand_sections,
        conversation_id=query_request.conversation_id,
    ):
        yield _format_sse(event, data)


@router.post("/query/stream")
def query_stream(query_request: GenerationQuery) -> StreamingResponse:
    """Run retrieval + LLM synthesis, streaming the answer as Server-Sent Events.

    Unlike `POST /generation/query`, failures surface as a terminal `error` SSE event
    (status stays 200, since headers are already sent once streaming starts) rather than
    an HTTP error status -- see `generate_stream`'s docstring.
    """
    return StreamingResponse(
        _event_stream(query_request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@conversations_router.get("/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID) -> ConversationHistoryResponse:
    """Return a conversation's full message history, oldest first, or 404 if unknown."""
    history = get_conversation_history(conversation_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return history
