"""Cache-aside storage for revoked refresh tokens, backed by Redis.

Never load-bearing (ADR-003): a Redis outage degrades `is_revoked` to `False`, so
`POST /auth/refresh` always falls back to the authoritative Postgres revocation check —
this cache only ever provides a fast-path short-circuit for the "known revoked" case.
"""

import logging
from functools import lru_cache
from typing import Protocol

import redis

from app.auth.config import AuthSettings, get_auth_settings

logger = logging.getLogger(__name__)


class RevocationCache(Protocol):
    """Anything that can cache-aside "is this refresh token hash revoked?"."""

    def is_revoked(self, token_hash: str) -> bool:
        """Return `True` if `token_hash` is cached as revoked. `False` on a cache miss too."""
        ...

    def mark_revoked(self, token_hash: str, ttl_seconds: int) -> None:
        """Cache `token_hash` as revoked for `ttl_seconds`. No-op if `ttl_seconds <= 0`."""
        ...


class RedisRevocationCache:
    """`RevocationCache` backed by Redis."""

    def __init__(self, settings: AuthSettings) -> None:
        """Build a cache bound to `settings.redis_url`."""
        self._client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )

    @staticmethod
    def _key(token_hash: str) -> str:
        return f"revoked_refresh_token:{token_hash}"

    def is_revoked(self, token_hash: str) -> bool:
        """Return `True` if `token_hash` is cached as revoked, else `False` (miss or error)."""
        try:
            return bool(self._client.exists(self._key(token_hash)))
        except redis.RedisError:
            logger.exception("Redis GET failed while checking revocation; falling back to Postgres")
            return False

    def mark_revoked(self, token_hash: str, ttl_seconds: int) -> None:
        """Cache `token_hash` as revoked for `ttl_seconds`. No-op on Redis error or non-positive TTL."""
        if ttl_seconds <= 0:
            return
        try:
            self._client.set(self._key(token_hash), "1", ex=ttl_seconds)
        except redis.RedisError:
            logger.exception("Redis SET failed while marking token revoked; continuing")


@lru_cache
def get_default_revocation_cache() -> RevocationCache:
    """Return the process-wide cached default `RevocationCache` (a `RedisRevocationCache`)."""
    return RedisRevocationCache(get_auth_settings())
