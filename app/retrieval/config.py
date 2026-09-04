"""Reranking settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class RerankerSettings(BaseSettings):
    """Configuration for the optional cross-encoder reranking step.

    Overridable via `RERANKER_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="RERANKER_")

    model_name: str = "ms-marco-TinyBERT-L-2-v2"
    cache_dir: str = "data/reranker_cache"


@lru_cache
def get_reranker_settings() -> RerankerSettings:
    """Return the process-wide cached `RerankerSettings` instance."""
    return RerankerSettings()


class RetrievalSettings(BaseSettings):
    """Configuration for the Redis-backed retrieval/query-result cache.

    Overridable via `RETRIEVAL_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="RETRIEVAL_")

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300


@lru_cache
def get_retrieval_settings() -> RetrievalSettings:
    """Return the process-wide cached `RetrievalSettings` instance."""
    return RetrievalSettings()
