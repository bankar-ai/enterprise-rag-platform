"""Generation API: grounded answer synthesis over retrieved chunks."""

import logging

from fastapi import APIRouter, HTTPException

from app.generation.schemas import GenerationQuery, GenerationResponse
from app.generation.service import generate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])


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
