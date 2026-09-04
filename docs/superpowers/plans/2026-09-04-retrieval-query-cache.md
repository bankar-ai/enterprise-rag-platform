# Redis Retrieval/Query-Result Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Redis cache-aside layer wrapping `app/retrieval/service.py`'s `search()`, so a repeated identical query (same `query`, `top_k`, `rerank`, `expand_sections`) skips the full hybrid retrieval pipeline and returns a cached result instead.

**Architecture:** Follows the ERP-013 embedding-cache pattern exactly: a `Protocol` + Redis implementation in a new `app/retrieval/cache.py`, a new `RetrievalSettings` in the existing `app/retrieval/config.py`, and the cache wired inside `search()` itself (checked first, populated on miss) so both existing callers — the `POST /retrieval/query` router and `app/generation/service.py`'s internal call — get caching transparently with zero changes to either.

**Tech Stack:** Python, `redis` (already a project dependency, ERP-013), `pydantic-settings`, pytest, real Redis in tests (no mocking, matching ERP-013's precedent).

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-query-cache-design.md`

## Global Constraints

- Cache is cache-aside and never load-bearing: every Redis failure must degrade to a miss (`get`) or silent no-op (`set`), never raise — per ADR-003.
- Cache key must hash all four parameters that affect `search()`'s output: `query`, `top_k`, `rerank`, `expand_sections`.
- `RETRIEVAL_CACHE_TTL_SECONDS` default: `300` (5 minutes). `RETRIEVAL_REDIS_URL` default: `redis://localhost:6379/0`.
- No changes to `app/retrieval/router.py` or `app/generation/service.py`.
- No new infra (reuse the existing `redis` service in `docker-compose.yml`/CI). No new dependency.
- 90% coverage gate (ERP-006) applies to all new/modified code.

---

### Task 1: `RetrievalSettings` config

**Files:**
- Modify: `app/retrieval/config.py`
- Test: `tests/retrieval/test_config.py` (new)

**Interfaces:**
- Produces: `RetrievalSettings` (class, `pydantic_settings.BaseSettings`) with fields `redis_url: str = "redis://localhost:6379/0"` and `cache_ttl_seconds: int = 300`, env prefix `RETRIEVAL_`. `get_retrieval_settings() -> RetrievalSettings`, `@lru_cache`-wrapped, mirroring `get_reranker_settings()` in the same file.

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_config.py
import os

from app.retrieval.config import RetrievalSettings, get_retrieval_settings


def test_retrieval_settings_defaults():
    settings = RetrievalSettings()
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cache_ttl_seconds == 300


def test_retrieval_settings_env_override(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_REDIS_URL", "redis://example:6380/2")
    monkeypatch.setenv("RETRIEVAL_CACHE_TTL_SECONDS", "60")
    settings = RetrievalSettings()
    assert settings.redis_url == "redis://example:6380/2"
    assert settings.cache_ttl_seconds == 60


def test_get_retrieval_settings_is_cached():
    assert get_retrieval_settings() is get_retrieval_settings()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/retrieval/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'RetrievalSettings'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/retrieval/config.py` (below the existing `RerankerSettings`/`get_reranker_settings`, same file):

```python
class RetrievalSettings(BaseSettings):
    """Configuration for the Redis-backed retrieval/query-result cache.

    Overridable via `RETRIEVAL_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="RETRIEVAL_")

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300


@lru_cache
def get_retrieval_settings() -> RetrievalSettings:
    """Return the process-wide cached `RetrievalSettings` instance."""
    return RetrievalSettings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/retrieval/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/retrieval/config.py tests/retrieval/test_config.py
git commit -m "feat: add RetrievalSettings for the Redis query-result cache (ERP-021)"
```

---

### Task 2: `RetrievalCache` protocol + `RedisRetrievalCache`

**Files:**
- Create: `app/retrieval/cache.py`
- Create: `tests/retrieval/conftest.py`
- Test: `tests/retrieval/test_cache.py` (new)

**Interfaces:**
- Consumes: `RetrievalSettings` from Task 1 (`app.retrieval.config.RetrievalSettings`, fields `redis_url: str`, `cache_ttl_seconds: int`). `RetrievedChunk` from `app.retrieval.schemas` (existing Pydantic model: `chunk_id: str`, `document_id: str`, `text: str`, `section_path: list[str]`, `page_start: int`, `page_end: int`, `source_filename: str`, `score: float`).
- Produces: `RetrievalCache` (`Protocol`) with `get(cache_key: str) -> list[RetrievedChunk] | None` and `set(cache_key: str, results: list[RetrievedChunk]) -> None`. `RedisRetrievalCache(settings: RetrievalSettings)` implementing it.

Redis in this project's tests runs on logical DB 1 for embedding-cache tests (`tests/embedding/conftest.py`); this task uses **DB 2** for retrieval-cache tests to avoid any collision.

- [ ] **Step 1: Write the shared test fixture**

```python
# tests/retrieval/conftest.py
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
```

- [ ] **Step 2: Write the failing test**

```python
# tests/retrieval/test_cache.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/retrieval/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.retrieval.cache'`

- [ ] **Step 4: Write minimal implementation**

```python
# app/retrieval/cache.py
"""Cache-aside storage for retrieval query results, backed by Redis.

Never load-bearing (ADR-003): every Redis failure degrades to a cache miss
(`get`) or a silent no-op (`set`) instead of raising.
"""

import logging
from typing import Protocol

import redis
from pydantic import TypeAdapter

from app.retrieval.config import RetrievalSettings
from app.retrieval.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

_results_adapter = TypeAdapter(list[RetrievedChunk])


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
        self._client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
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
            return None
        if raw is None:
            return None
        return _results_adapter.validate_json(raw)

    def set(self, cache_key: str, results: list[RetrievedChunk]) -> None:
        """Cache `results` for `cache_key` with the configured TTL. No-op on Redis error."""
        try:
            self._client.set(
                self._key(cache_key), _results_adapter.dump_json(results), ex=self._ttl_seconds
            )
        except redis.RedisError:
            logger.exception("Redis SET failed; continuing without caching this result")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/retrieval/test_cache.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add app/retrieval/cache.py tests/retrieval/conftest.py tests/retrieval/test_cache.py
git commit -m "feat: add RedisRetrievalCache for retrieval query results (ERP-021)"
```

---

### Task 3: Wire the cache into `search()`

**Files:**
- Modify: `app/retrieval/service.py`
- Test: `tests/retrieval/test_service.py`

**Interfaces:**
- Consumes: `RetrievalCache`, `RedisRetrievalCache` from Task 2 (`app.retrieval.cache`). `get_retrieval_settings` from Task 1 (`app.retrieval.config`).
- Produces: `search(...)` gains a `cache: RetrievalCache | None = None` keyword parameter (defaulting to `RedisRetrievalCache(get_retrieval_settings())`), inserted after the existing `expand_sections: bool = False` parameter. New module-level `_cache_key(query: str, top_k: int, rerank: bool, expand_sections: bool) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/retrieval/test_service.py` (alongside the existing fakes and tests):

```python
class _FakeRetrievalCache:
    def __init__(self, preset: dict[str, list] | None = None):
        self.store = dict(preset or {})
        self.get_calls = []
        self.set_calls = []

    def get(self, cache_key):
        self.get_calls.append(cache_key)
        return self.store.get(cache_key)

    def set(self, cache_key, results):
        self.set_calls.append((cache_key, results))
        self.store[cache_key] = results


def test_search_returns_cached_result_without_touching_pipeline(tmp_path):
    cached_results = [
        RetrievedChunk(
            chunk_id="cached-0",
            document_id="doc-cached",
            text="cached text",
            section_path=["Intro"],
            page_start=1,
            page_end=1,
            source_filename="doc.pdf",
            score=1.0,
        )
    ]
    fake_cache = _FakeRetrievalCache()
    cache_key = service_module._cache_key("cached query", 5, False, False)
    fake_cache.store[cache_key] = cached_results

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)

    results = search(
        query="cached query",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
        cache=fake_cache,
    )

    assert results == cached_results
    assert fake_client.calls == []  # embedding client never called on a cache hit


def test_search_populates_cache_on_miss(tmp_path):
    document_id = "doc-cache-miss-test"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_cache = _FakeRetrievalCache()

    results = search(
        query="find it",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
        cache=fake_cache,
    )

    cache_key = service_module._cache_key("find it", 5, False, False)
    assert fake_cache.get_calls == [cache_key]
    assert fake_cache.set_calls == [(cache_key, results)]


def test_search_caches_empty_result_on_miss(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_cache = _FakeRetrievalCache()

    results = search(
        query="anything",
        top_k=5,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index=faiss_index,
        cache=fake_cache,
    )

    cache_key = service_module._cache_key("anything", 5, False, False)
    assert results == []
    assert fake_cache.set_calls == [(cache_key, [])]


def test_cache_key_differs_by_rerank_and_expand_sections_flags():
    base = service_module._cache_key("q", 5, False, False)
    assert service_module._cache_key("q", 5, True, False) != base
    assert service_module._cache_key("q", 5, False, True) != base
    assert service_module._cache_key("q", 10, False, False) != base
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/retrieval/test_service.py -v -k "cache"`
Expected: FAIL — `AttributeError: module 'app.retrieval.service' has no attribute '_cache_key'` (and `search()` raising `TypeError: unexpected keyword argument 'cache'`)

- [ ] **Step 3: Write minimal implementation**

In `app/retrieval/service.py`, add the import and the `_cache_key` function near the top (after the existing imports/constants), and modify `search()`:

```python
import hashlib
```
(add to the existing `import logging` line's import block)

```python
from app.retrieval.cache import RedisRetrievalCache, RetrievalCache
from app.retrieval.config import get_reranker_settings, get_retrieval_settings
```
(extend the existing `from app.retrieval.config import get_reranker_settings` line to also import `get_retrieval_settings`)

```python
def _cache_key(query: str, top_k: int, rerank: bool, expand_sections: bool) -> str:
    """Hash the four `search()` parameters that determine its output, for cache lookups."""
    query_bytes = query.encode()
    payload = (
        len(query_bytes).to_bytes(4, "big")
        + query_bytes
        + top_k.to_bytes(4, "big")
        + bytes([rerank, expand_sections])
    )
    return hashlib.sha256(payload).hexdigest()
```

Modify `search()`'s signature (add `cache` as the last parameter) and body:

```python
def search(
    query: str,
    top_k: int,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
    rerank: bool = False,
    reranker: Reranker | None = None,
    expand_sections: bool = False,
    cache: RetrievalCache | None = None,
) -> list[RetrievedChunk]:
    """Run hybrid (vector + BM25) search and return up to `top_k` chunks, fused-score order.

    ... (existing docstring content unchanged) ...

    `cache` is an injectable `RetrievalCache` (defaulting to `RedisRetrievalCache`); the full
    result of this function, keyed by (`query`, `top_k`, `rerank`, `expand_sections`), is
    cache-aside -- a hit returns immediately without running any of the pipeline below.
    """
    cache = cache or RedisRetrievalCache(get_retrieval_settings())
    cache_key = _cache_key(query, top_k, rerank, expand_sections)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    candidate_k = top_k * RRF_OVERSAMPLE_MULTIPLIER

    vectors = embedding_client.embed([query])
    if not vectors:
        raise ValueError("embedding client returned no vectors for the query")
    vector_hits = faiss_index.search(vectors[0], candidate_k)
    vector_ranked_ids = [vector_id for vector_id, _ in vector_hits]

    session_factory = get_session_factory()
    with session_factory() as session:
        bm25_hits = search_chunks_by_text(session, query, candidate_k)
        bm25_ranked_ids = [vector_id for vector_id, _ in bm25_hits]

        fused = _reciprocal_rank_fusion(vector_ranked_ids, bm25_ranked_ids)[:top_k]
        if not fused:
            cache.set(cache_key, [])
            return []

        chunks_by_vector_id = get_chunks_by_vector_ids(session, [vector_id for vector_id, _ in fused])
        results = []
        for vector_id, score in fused:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
                logger.warning("Dropping fused hit with no matching chunk row: vector_id=%s", vector_id)
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_filename=chunk.source_filename,
                    score=score,
                )
            )

        if rerank:
            reranker = reranker or FlashRankReranker(get_reranker_settings())
            results = reranker.rerank(query, results)

        if expand_sections:
            results = _expand_sections(session, results)

        cache.set(cache_key, results)
        return results
```

Note: the early-return-on-empty-`fused` branch now also calls `cache.set(cache_key, [])` before returning, so an empty-index/empty-match query is cached too (per spec).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/retrieval/test_service.py -v`
Expected: PASS (all existing tests still pass — they don't inject `cache`, so `RedisRetrievalCache` is constructed by default; since `tests/retrieval/conftest.py` from Task 2 points `RETRIEVAL_REDIS_URL` at a real test-only Redis DB and every test flushes it, this stays a real miss-then-populate for those tests, not an error)

- [ ] **Step 5: Commit**

```bash
git add app/retrieval/service.py tests/retrieval/test_service.py
git commit -m "feat: wire RedisRetrievalCache into search() (ERP-021)"
```

---

### Task 4: Full verification and ticket close-out

**Files:**
- Modify: `.ai/tickets/ERP-021.md` (check off acceptance criteria, set `Status: Done`)
- Modify: `.ai/memory/current-state.md` (add ERP-021 bullet, update "Next Planned Work")

**Interfaces:**
- Consumes: nothing new — this task verifies the whole feature end-to-end.

- [ ] **Step 1: Run the full test suite with coverage**

Run: `uv run pytest --cov=app --cov-fail-under=90`
Expected: all tests pass (existing suite + the new cache/config/service tests from Tasks 1-3), coverage gate holds

- [ ] **Step 2: Run lint and type checks**

Run: `uv run ruff check .` and `uv run mypy app/`
Expected: no errors

- [ ] **Step 3: Manual smoke check against local docker-compose services**

Start local services (`docker-compose up -d postgres redis`), run the app, issue the same `POST /retrieval/query` request twice with identical body, and confirm (via a temporary debug log line, removed before commit, or by observing latency drop) that the second call is a cache hit. This step is exploratory — no code changes expected unless it surfaces a bug.

- [ ] **Step 4: Update ticket and current-state.md**

In `.ai/tickets/ERP-021.md`: check off every acceptance criterion, set `Status: Done`.

In `.ai/memory/current-state.md`: add a new bullet (in the same style as the ERP-013/014/... bullets) describing what ERP-021 built, and update "Next Planned Work" to remove the retrieval/query-result cache line (keep the session/auth-token cache line, noting it's blocked on Authentication).

- [ ] **Step 5: Commit**

```bash
git add .ai/tickets/ERP-021.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-021 with updated ticket and current-state (ERP-021)"
```
