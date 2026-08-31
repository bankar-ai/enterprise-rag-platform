"""Shared fixtures for embedding tests: points Redis at a dedicated test-only logical DB."""

import os

os.environ.setdefault("EMBEDDING_REDIS_URL", "redis://localhost:6379/1")

import pytest  # noqa: E402
import redis  # noqa: E402

from app.embedding.config import EmbeddingSettings  # noqa: E402


@pytest.fixture
def redis_settings() -> EmbeddingSettings:
    """`EmbeddingSettings` pointed at the dedicated test Redis logical DB."""
    return EmbeddingSettings(redis_url=os.environ["EMBEDDING_REDIS_URL"])


@pytest.fixture(autouse=True)
def _flush_test_redis_db(redis_settings: EmbeddingSettings):
    """Flush the test-only Redis logical DB before and after every embedding test."""
    client = redis.Redis.from_url(redis_settings.redis_url)
    client.flushdb()
    yield
    client.flushdb()
