"""Tests for the refresh-token revocation cache."""

from app.auth.cache import RedisRevocationCache


def test_mark_revoked_then_is_revoked_returns_true(auth_settings):
    cache = RedisRevocationCache(auth_settings)
    assert cache.is_revoked("some-hash") is False

    cache.mark_revoked("some-hash", ttl_seconds=60)

    assert cache.is_revoked("some-hash") is True


def test_mark_revoked_with_zero_ttl_is_a_no_op(auth_settings):
    cache = RedisRevocationCache(auth_settings)
    cache.mark_revoked("some-hash", ttl_seconds=0)
    assert cache.is_revoked("some-hash") is False


def test_is_revoked_degrades_to_false_on_redis_error(auth_settings, monkeypatch):
    import redis

    cache = RedisRevocationCache(auth_settings)

    def _raise(*args, **kwargs):
        raise redis.RedisError("connection refused")

    monkeypatch.setattr(cache._client, "exists", _raise)
    assert cache.is_revoked("some-hash") is False
