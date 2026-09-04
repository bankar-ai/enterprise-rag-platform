from app.retrieval.cache import RedisRetrievalCache
from app.retrieval.config import RetrievalSettings
from app.retrieval.schemas import RetrievedChunk


def _chunk(chunk_id: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=score,
    )


def test_get_returns_none_on_miss(redis_settings):
    cache = RedisRetrievalCache(redis_settings)
    assert cache.get("key-a") is None


def test_set_then_get_round_trips_results(redis_settings):
    cache = RedisRetrievalCache(redis_settings)
    results = [_chunk("c1", 0.9), _chunk("c2", 0.5)]
    cache.set("key-a", results)
    assert cache.get("key-a") == results


def test_set_then_get_round_trips_empty_list(redis_settings):
    cache = RedisRetrievalCache(redis_settings)
    cache.set("key-empty", [])
    assert cache.get("key-empty") == []


def test_different_keys_do_not_collide(redis_settings):
    cache = RedisRetrievalCache(redis_settings)
    cache.set("key-a", [_chunk("c1", 0.9)])
    assert cache.get("key-b") is None


def test_unreachable_redis_degrades_to_miss_and_noop_set():
    settings = RetrievalSettings(redis_url="redis://localhost:1/0")
    cache = RedisRetrievalCache(settings)
    assert cache.get("key-a") is None
    cache.set("key-a", [_chunk("c1", 0.9)])  # must not raise
