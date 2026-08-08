"""Semantic search over ingested chunks: embed a query, search FAISS, hydrate from Postgres."""

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import FaissIndex
from app.ingestion.repository import get_chunks_by_vector_ids
from app.retrieval.schemas import RetrievedChunk


def search(
    query: str,
    top_k: int,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> list[RetrievedChunk]:
    """Embed `query`, search the FAISS index, and return up to `top_k` chunks ranked by similarity.

    `embedding_client`/`faiss_index`/`settings` are injectable for testing; default to
    Ollama/local-disk implementations built from `settings` (or the process-wide cached
    `EmbeddingSettings` if `settings` is not given).
    """
    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    vectors = embedding_client.embed([query])
    hits = faiss_index.search(vectors[0], top_k)
    if not hits:
        return []

    session_factory = get_session_factory()
    with session_factory() as session:
        chunks_by_vector_id = get_chunks_by_vector_ids(session, [vector_id for vector_id, _ in hits])
        results = []
        for vector_id, distance in hits:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
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
                    score=1.0 / (1.0 + distance),
                )
            )
        return results
