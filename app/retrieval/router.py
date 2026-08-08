"""Retrieval API: semantic search over ingested chunks."""

from fastapi import APIRouter

from app.retrieval.schemas import RetrievalQuery, RetrievalResponse
from app.retrieval.service import search

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/query")
def query(request: RetrievalQuery) -> RetrievalResponse:
    """Run a semantic search query and return ranked matching chunks."""
    results = search(request.query, request.top_k)
    return RetrievalResponse(results=results)
