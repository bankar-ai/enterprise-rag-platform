"""Cache-aside storage for retrieval query results, backed by Redis.

Never load-bearing (ADR-003): every Redis failure degrades to a cache miss
(`get`) or a silent no-op (`set`) instead of raising.
"""

import logging
from functools import lru_cache
from typing import Protocol

import redis
from pydantic import TypeAdapter

from app.core.telemetry import get_meter
from app.retrieval.config import RetrievalSettings, get_retrieval_settings
from app.retrieval.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

_results_adapter = TypeAdapter(list[RetrievedChunk])

_meter = get_meter()
_cache_requests_counter = _meter.create_counter(
    "retrieval_cache_requests_total", description="Retrieval cache lookups by result"
)


class RetrievalCache(Protocol):
    """Anything that can cache-aside a `search()` result for a given cache key."""

    def get(self, cache_key: str) -> list[RetrievedChunk] | None:
        """Return the cached results for `cache_key`, or `None` on a miss."""
        ...

    def set(self, cache_key: str, results: list[RetrievedChunk]) -> None:
        """Cache `results` for `cache_key`."""
        ...


class RedisRetrievalCache:
    """`RetrievalCache` backed by Redis."""

    def __init__(self, settings: RetrievalSettings) -> None:
        """Build a cache bound to `settings.redis_url`, TTL from `settings.cache_ttl_seconds`."""
        self._client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self._ttl_seconds = settings.cache_ttl_seconds

    @staticmethod
    def _key(cache_key: str) -> str:
        return f"retrieval:{cache_key}"

    def get(self, cache_key: str) -> list[RetrievedChunk] | None:
        """Return the cached results for `cache_key`, or `None` on a miss or Redis error."""
        try:
            raw = self._client.get(self._key(cache_key))
        except redis.RedisError:
            logger.exception("Redis GET failed; treating as a cache miss")
            _cache_requests_counter.add(1, {"result": "miss"})
            return None
        if raw is None:
            _cache_requests_counter.add(1, {"result": "miss"})
            return None
        try:
            results = _results_adapter.validate_json(raw)
        except (ValueError, UnicodeDecodeError):
            logger.exception("Failed to deserialize cached retrieval result; treating as a cache miss")
            _cache_requests_counter.add(1, {"result": "miss"})
            return None
        _cache_requests_counter.add(1, {"result": "hit"})
        return results

    def set(self, cache_key: str, results: list[RetrievedChunk]) -> None:
        """Cache `results` for `cache_key` with the configured TTL. No-op on Redis error."""
        try:
            self._client.set(
                self._key(cache_key), _results_adapter.dump_json(results), ex=self._ttl_seconds
            )
        except redis.RedisError:
            logger.exception("Redis SET failed; continuing without caching this result")


@lru_cache
def get_default_retrieval_cache() -> RetrievalCache:
    """Return the process-wide cached default `RetrievalCache` (a `RedisRetrievalCache`).

    Memoized so `search()` reuses one Redis client/connection pool across calls instead of
    building a new one every time it falls back to the default cache.
    """
    return RedisRetrievalCache(get_retrieval_settings())
