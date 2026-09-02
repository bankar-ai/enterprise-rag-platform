"""Generation API: grounded answer synthesis over retrieved chunks."""

import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.generation.schemas import ConversationHistoryResponse, GenerationQuery, GenerationResponse
from app.generation.service import generate, get_conversation_history

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


@conversations_router.get("/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID) -> ConversationHistoryResponse:
    """Return a conversation's full message history, oldest first, or 404 if unknown."""
    history = get_conversation_history(conversation_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return history
