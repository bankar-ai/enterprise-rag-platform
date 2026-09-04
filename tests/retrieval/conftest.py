"""Shared fixtures for retrieval tests: points Redis at a dedicated test-only logical DB."""

import os

os.environ.setdefault("RETRIEVAL_REDIS_URL", "redis://localhost:6379/2")

import pytest  # noqa: E402
import redis  # noqa: E402

from app.retrieval.config import RetrievalSettings  # noqa: E402


@pytest.fixture
def redis_settings() -> RetrievalSettings:
    """`RetrievalSettings` pointed at the dedicated test Redis logical DB."""
    return RetrievalSettings(redis_url=os.environ["RETRIEVAL_REDIS_URL"])


@pytest.fixture(autouse=True)
def _flush_test_redis_db(redis_settings: RetrievalSettings):
    """Flush the test-only Redis logical DB before and after every retrieval test."""
    client = redis.Redis.from_url(redis_settings.redis_url)
    client.flushdb()
    yield
    client.flushdb()
