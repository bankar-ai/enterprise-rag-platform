import redis

from app.embedding.cache import RedisEmbeddingCache, get_default_embedding_cache
from app.embedding.config import EmbeddingSettings


def test_get_returns_none_on_miss(redis_settings):
    cache = RedisEmbeddingCache(redis_settings)
    assert cache.get("model-a", "hello world") is None


def test_set_then_get_round_trips_vector(redis_settings):
    cache = RedisEmbeddingCache(redis_settings)
    cache.set("model-a", "hello world", [0.1, 0.2, 0.3])
    assert cache.get("model-a", "hello world") == [0.1, 0.2, 0.3]


def test_same_text_different_model_does_not_collide(redis_settings):
    cache = RedisEmbeddingCache(redis_settings)
    cache.set("model-a", "hello world", [0.1, 0.2])
    assert cache.get("model-b", "hello world") is None


def test_unreachable_redis_degrades_to_miss_and_noop_set():
    settings = EmbeddingSettings(redis_url="redis://localhost:1/0")
    cache = RedisEmbeddingCache(settings)
    assert cache.get("model-a", "text") is None
    cache.set("model-a", "text", [0.1])  # must not raise


def test_socket_timeouts_are_passed_to_the_redis_client(redis_settings):
    settings = redis_settings.model_copy(update={"redis_socket_timeout_seconds": 0.75})
    cache = RedisEmbeddingCache(settings)
    connection_kwargs = cache._client.connection_pool.connection_kwargs
    assert connection_kwargs["socket_connect_timeout"] == 0.75
    assert connection_kwargs["socket_timeout"] == 0.75


def test_corrupt_cached_value_degrades_to_miss(redis_settings):
    client = redis.Redis.from_url(redis_settings.redis_url, decode_responses=True)
    client.set(RedisEmbeddingCache._key("model-a", "text"), "not valid json at all {")

    cache = RedisEmbeddingCache(redis_settings)
    assert cache.get("model-a", "text") is None


def test_get_default_embedding_cache_is_memoized():
    get_default_embedding_cache.cache_clear()
    try:
        assert get_default_embedding_cache() is get_default_embedding_cache()
    finally:
        get_default_embedding_cache.cache_clear()
