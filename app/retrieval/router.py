"""Retrieval API: semantic search over ingested chunks."""

import logging

from fastapi import APIRouter, HTTPException

from app.retrieval.schemas import RetrievalQuery, RetrievalResponse
from app.retrieval.service import search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/query")
def query(query_request: RetrievalQuery) -> RetrievalResponse:
    """Run a semantic search query and return ranked matching chunks."""
    try:
        results = search(query_request.query, query_request.top_k, rerank=query_request.rerank)
    except Exception as exc:
        logger.exception("Retrieval query failed")
        raise HTTPException(status_code=503, detail="Retrieval query failed") from exc
    return RetrievalResponse(results=results)
