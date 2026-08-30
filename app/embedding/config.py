"""Embedding generation settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    """Configuration for embedding generation and vector index storage.

    Overridable via `EMBEDDING_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_")

    ollama_host: str = "http://localhost:11434"
    model: str = "nomic-embed-text"
    dimension: int = 768
    faiss_index_path: str = "data/faiss_index.bin"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 86400


@lru_cache
def get_embedding_settings() -> EmbeddingSettings:
    """Return the process-wide cached `EmbeddingSettings` instance."""
    return EmbeddingSettings()
