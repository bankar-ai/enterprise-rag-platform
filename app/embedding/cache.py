"""Cache-aside storage for embedding vectors, backed by Redis.

Never load-bearing (ADR-003): every Redis failure degrades to a cache miss
(`get`) or a silent no-op (`set`) instead of raising.
"""

import hashlib
import json
import logging
from typing import Protocol

import redis

from app.embedding.config import EmbeddingSettings

logger = logging.getLogger(__name__)


class EmbeddingCache(Protocol):
    """Anything that can cache-aside an embedding vector for a `(model, text)` pair."""

    def get(self, model: str, text: str) -> list[float] | None:
        """Return the cached vector for `(model, text)`, or `None` on a miss."""
        ...

    def set(self, model: str, text: str, vector: list[float]) -> None:
        """Cache `vector` for `(model, text)`."""
        ...


class RedisEmbeddingCache:
    """`EmbeddingCache` backed by Redis, keyed by a hash of `(model, text)`."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        """Build a cache bound to `settings.redis_url`, TTL from `settings.cache_ttl_seconds`."""
        self._client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._ttl_seconds = settings.cache_ttl_seconds

    @staticmethod
    def _key(model: str, text: str) -> str:
        digest = hashlib.sha256(f"{model}:{text}".encode()).hexdigest()
        return f"embedding:{digest}"

    def get(self, model: str, text: str) -> list[float] | None:
        """Return the cached vector for `(model, text)`, or `None` on a miss or Redis error."""
        try:
            raw = self._client.get(self._key(model, text))
        except redis.RedisError:
            logger.exception("Redis GET failed; treating as a cache miss")
            return None
        if raw is None:
            return None
        vector: list[float] = json.loads(raw)
        return vector

    def set(self, model: str, text: str, vector: list[float]) -> None:
        """Cache `vector` for `(model, text)` with the configured TTL. No-op on Redis error."""
        try:
            self._client.set(self._key(model, text), json.dumps(vector), ex=self._ttl_seconds)
        except redis.RedisError:
            logger.exception("Redis SET failed; continuing without caching this vector")
