"""Cross-encoder reranking over retrieval candidates, via a local FlashRank model."""

from typing import Protocol

from flashrank import Ranker, RerankRequest

from app.retrieval.config import RerankerSettings
from app.retrieval.schemas import RetrievedChunk


class Reranker(Protocol):
    """Anything that can reorder retrieval candidates by relevance to `query`."""

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Return `candidates` reordered best-first, with `score` reflecting the new ranking."""
        ...


class FlashRankReranker:
    """`Reranker` backed by a local FlashRank cross-encoder (ONNX, no `torch`)."""

    def __init__(self, settings: RerankerSettings) -> None:
        """Build a reranker using `settings.model_name`, caching model weights in `settings.cache_dir`."""
        self._ranker = Ranker(model_name=settings.model_name, cache_dir=settings.cache_dir)

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Score each candidate's text against `query` and return them best-first.

        `[]` for an empty `candidates` list, without invoking the model.
        """
        if not candidates:
            return []
        request = RerankRequest(
            query=query,
            passages=[{"id": index, "text": candidate.text} for index, candidate in enumerate(candidates)],
        )
        ranked = self._ranker.rerank(request)
        return [
            candidates[result["id"]].model_copy(update={"score": float(result["score"])}) for result in ranked
        ]
