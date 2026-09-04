# Redis Retrieval/Query-Result Cache Design

Date: 2026-09-04

## Context

ADR-003 ("Data Layer & Caching Architecture") scoped Redis to three cache-aside uses: an embedding cache (ERP-013, done), a retrieval/query-result cache (short TTL), and a session/auth-token cache. `.ai/memory/current-state.md`'s "Next Planned Work" names the retrieval/query-result cache as the next item.

Today, `app/retrieval/service.py`'s `search()` runs the full hybrid pipeline (embed query → FAISS search → Postgres BM25 search → RRF fusion → hydrate → optional rerank → optional section expansion) on every call, even for a repeated identical query. The embedding sub-step already benefits from ERP-013's cache, but everything downstream (two searches, fusion, hydration, and the optional cross-encoder rerank pass — the most expensive step) is redone from scratch every time.

`app/retrieval/service.py`'s `search()` has two callers: the `POST /retrieval/query` router, and `app/generation/service.py` (both `generate()` and `generate_stream()`), which calls it internally as part of answering a question. A cache wired inside `search()` itself benefits both transparently, mirroring how ERP-013's embedding cache lives inside `OllamaEmbeddingClient` rather than at each call site.

## Scope

In scope: a Redis-backed cache-aside layer wrapping the full output of `search()`, keyed by all parameters that affect its result (`query`, `top_k`, `rerank`, `expand_sections`), wired transparently into `search()` so neither caller needs to change. Graceful degradation when Redis is unreachable (always treated as a miss, never an error) — same contract as the embedding cache.

Out of scope: the session/auth-token cache ADR-003 also names for Redis. No authentication system exists yet in this project to issue or validate tokens for, so there is nothing to cache — this stays a separate, blocked follow-up until an Authentication ticket exists. Cache invalidation on new ingestion (e.g. busting cached results for documents affected by a new upload) — staleness is bounded purely by a short TTL, per ADR-003's own wording ("short TTL"), matching the embedding cache's TTL-only approach.

## Module Layout

Following the existing `app/retrieval/` structure:

- `app/retrieval/config.py` — modified. Gains a `RetrievalSettings` (`BaseSettings`, `RETRIEVAL_`-prefixed env vars, `@lru_cache`-wrapped `get_retrieval_settings()`), following the same shape as the existing `RerankerSettings` in this file: `redis_url: str` (default `redis://localhost:6379/0`) and `cache_ttl_seconds: int` (default `300`, i.e. 5 minutes — short by design, since query results go stale as new documents are ingested, unlike the embedding cache's 24h TTL for a deterministic `(model, text) -> vector` mapping).
- `app/retrieval/cache.py` — new. `RetrievalCache` (`Protocol` with `get(cache_key: str) -> list[RetrievedChunk] | None` and `set(cache_key: str, results: list[RetrievedChunk]) -> None`) and `RedisRetrievalCache(settings: RetrievalSettings)` implementing it via the `redis` Python client (already a project dependency, ERP-013). Every Redis call wrapped in `try/except redis.RedisError`, logged, degrading to a miss (`get`) or silent no-op (`set`) — never load-bearing, per ADR-003.
- `app/retrieval/service.py` — modified. New module-level `_cache_key(query, top_k, rerank, expand_sections) -> str`: sha256 of the four fields, length-prefixed (mirrors `RedisEmbeddingCache._key`'s `(model, text)` collision-safety approach, extended to four fields instead of two), prefixed `f"retrieval:{digest}"`. `search()` gains an injectable `cache: RetrievalCache | None = None` parameter, defaulting to `RedisRetrievalCache(get_retrieval_settings())`. At the top of `search()`, before embedding the query, check `cache.get(cache_key)`; return immediately on a hit. On a miss, run the existing pipeline unchanged, then `cache.set(cache_key, results)` immediately before returning.

No changes to `app/retrieval/router.py` or `app/generation/service.py` — both already call `search(...)` positionally/by keyword without a `cache` argument, so they get caching automatically.

## Data Flow

`search(query, top_k, ..., rerank, expand_sections)` → compute `cache_key` from the four param values → `RedisRetrievalCache.get(cache_key)` → hit: return the cached `list[RetrievedChunk]` immediately, skipping embedding, FAISS, Postgres BM25, fusion, hydration, rerank, and expansion entirely → miss: run today's full pipeline unchanged, producing `results` → `RedisRetrievalCache.set(cache_key, results)` (fire-and-forget; a `set` failure is logged and ignored) → return `results`.

The empty-query-guard and empty-index-returns-`[]` behaviors are unaffected: an empty `results` list is itself cached (a repeated query against an empty index is also worth not re-computing), consistent with treating the cache as an exact memoization of `search()`'s return value for a given key.

## Config

New `RetrievalSettings` fields, `RETRIEVAL_`-prefixed:

- `RETRIEVAL_REDIS_URL` — default `redis://localhost:6379/0`. Same default Redis instance as the embedding cache (ERP-013) and reranker settings' existing conventions in this module, but its own setting rather than reused from `EmbeddingSettings` — keeps `app/retrieval/` and `app/embedding/` decoupled in config, consistent with how `RerankerSettings` already duplicates rather than imports from `EmbeddingSettings`.
- `RETRIEVAL_CACHE_TTL_SECONDS` — default `300` (5 minutes). Short enough that newly-ingested documents show up in retrieval results within a bounded, small window without any explicit invalidation; long enough to absorb bursts of repeated/near-duplicate queries (e.g. a user re-running the same question, or `generate()`'s internal call re-querying what the retrieval endpoint just computed).

## Infra

No new infra: reuses the `redis` service already added to `docker-compose.yml` and CI by ERP-013. No new CI step.

## Testing

- `app/retrieval/cache.py`: new `tests/retrieval/test_cache.py`, run against a real Redis (same pattern as `tests/embedding/test_cache.py` — a dedicated logical DB via a fixture that flushes before/after each test, avoiding collisions with app data or the embedding cache's own keys). Covers: miss returns `None`, set-then-get round-trips a `list[RetrievedChunk]`, differing `top_k`/`rerank`/`expand_sections` for the same query produce different keys (no collision), and a cache pointed at an unreachable host degrades to a miss/no-op instead of raising.
- `app/retrieval/service.py`: `tests/retrieval/test_service.py` extended with an injected fake `RetrievalCache` (in-memory dict), keeping existing tests fast/isolated. New tests cover: a cache hit returns the cached value without calling the embedding client, FAISS index, or Postgres session at all; a cache miss runs the full pipeline and writes the result back; an empty-results miss is still cached.
- `app/retrieval/config.py`: `tests/retrieval/test_config.py` (new, or extended if it already covers `RerankerSettings`) gets defaults + env-var-override assertions for the two new fields.
- The 90%-coverage CI gate (ERP-006) applies to all new/modified code.

## New Dependencies

None — `redis` is already a project dependency (added in ERP-013).

## Known Limitations

**No cache invalidation on ingestion.** A newly-ingested document won't appear in a cached query's results until that cache entry's TTL (5 minutes) expires. Accepted per ADR-003's "short TTL" framing — bounding staleness via TTL alone, rather than invalidating on write, keeps the cache simple and consistent with the embedding cache's approach; revisit only if 5 minutes proves too slow in practice.

**Session/auth-token cache remains unbuilt.** ADR-003 names it as a third Redis use; it stays blocked until an Authentication ticket exists to define what a "session/auth token" even is in this project. Not addressed here.
