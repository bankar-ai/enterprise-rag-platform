from app.embedding.cache import RedisEmbeddingCache
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
