"""Hybrid search over ingested chunks.

Fuses FAISS vector search and Postgres BM25 full-text search via Reciprocal Rank Fusion (RRF),
then hydrates the fused results from Postgres. See `.ai/adr/ADR-005.md` for why RRF was chosen
over score-normalization-based fusion.
"""

import logging

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import FaissIndex
from app.ingestion.repository import get_chunks_by_vector_ids, search_chunks_by_text
from app.retrieval.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

# The constant from the original RRF paper (Cormack et al.), and the value most hybrid-search
# implementations default to. Dampens the influence of low ranks without per-corpus tuning.
RRF_K = 60

# Each retriever is asked for RRF_OVERSAMPLE_MULTIPLIER * top_k candidates before fusion, so a
# chunk that ranks just outside top_k on one retriever but strongly on the other still has a
# chance to fuse into the final top_k. Without oversampling, fusion only ever sees the union of
# two already-truncated top_k lists, defeating the point of combining two signals.
RRF_OVERSAMPLE_MULTIPLIER = 4


def _reciprocal_rank_fusion(*ranked_id_lists: list[int], k: int = RRF_K) -> list[tuple[int, float]]:
    """Fuse multiple rank-ordered ID lists into one, scored by normalized reciprocal rank.

    Each `ranked_id_lists` entry is a best-first list of IDs from one retriever. An ID's raw
    contribution from a given list is `1 / (k + rank)` (1-indexed); IDs absent from a list
    contribute nothing for that list. Raw sums are then divided by the maximum score achievable
    (an ID ranked first in every non-empty list), so the fused score is bounded to `(0, 1]` and
    comparable across queries -- `1.0` means "best possible rank in every retriever that found
    it". This is a fusion-confidence score, not a similarity/distance metric. Returns `(id,
    normalized_score)` pairs sorted by score descending. `[]` if every input list is empty.
    """
    scores: dict[int, float] = {}
    for ranked_ids in ranked_id_lists:
        for rank, item_id in enumerate(ranked_ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    if not scores:
        return []
    max_possible_score = sum(1.0 / (k + 1) for ranked_ids in ranked_id_lists if ranked_ids)
    normalized = [(item_id, score / max_possible_score) for item_id, score in scores.items()]
    return sorted(normalized, key=lambda pair: pair[1], reverse=True)


def search(
    query: str,
    top_k: int,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> list[RetrievedChunk]:
    """Run hybrid (vector + BM25) search and return up to `top_k` chunks, fused-score order.

    `embedding_client`/`faiss_index`/`settings` are injectable for testing; default to
    Ollama/local-disk implementations built from `settings` (or the process-wide cached
    `EmbeddingSettings` if `settings` is not given).
    """
    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    candidate_k = top_k * RRF_OVERSAMPLE_MULTIPLIER

    vectors = embedding_client.embed([query])
    if not vectors:
        raise ValueError("embedding client returned no vectors for the query")
    vector_hits = faiss_index.search(vectors[0], candidate_k)
    vector_ranked_ids = [vector_id for vector_id, _ in vector_hits]

    session_factory = get_session_factory()
    with session_factory() as session:
        bm25_hits = search_chunks_by_text(session, query, candidate_k)
        bm25_ranked_ids = [vector_id for vector_id, _ in bm25_hits]

        fused = _reciprocal_rank_fusion(vector_ranked_ids, bm25_ranked_ids)[:top_k]
        if not fused:
            return []

        chunks_by_vector_id = get_chunks_by_vector_ids(session, [vector_id for vector_id, _ in fused])
        results = []
        for vector_id, score in fused:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
                logger.warning("Dropping fused hit with no matching chunk row: vector_id=%s", vector_id)
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_filename=chunk.source_filename,
                    score=score,
                )
            )
        return results
