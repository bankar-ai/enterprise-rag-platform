"""Orchestrates embedding a document's chunks and persisting them to Postgres + FAISS."""

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import FaissIndex
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk


def embed_and_persist(
    document_id: str,
    source_filename: str,
    chunks: list[Chunk],
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> None:
    """Embed `chunks`, persist them to Postgres, and add their vectors to the FAISS index.

    No-op if `chunks` is empty. `embedding_client`/`faiss_index` are injectable for testing;
    default to Ollama/local-disk implementations built from `settings` (or the process-wide
    cached `EmbeddingSettings` if `settings` is not given).
    """
    if not chunks:
        return

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    vectors = embedding_client.embed([chunk.text for chunk in chunks])

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, source_filename, chunks)
        vector_ids = [record.vector_id for record in records]
        session.commit()

    faiss_index.add(vector_ids, vectors)
    faiss_index.save()
