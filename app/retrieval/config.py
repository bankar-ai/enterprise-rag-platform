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
