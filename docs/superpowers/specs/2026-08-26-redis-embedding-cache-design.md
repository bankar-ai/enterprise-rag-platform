# Redis Embedding Cache Design

Date: 2026-08-26

## Context

ADR-003 ("Data Layer & Caching Architecture") decided Redis as a cache-aside layer scoped to three uses, one of which is "an embedding cache (skip re-embedding duplicate chunk content by hash)". ERP-011 built the Ollama-backed embedding client and FAISS/Postgres persistence but explicitly deferred Redis: "it's cache-aside and non-load-bearing, so adding it later changes nothing about correctness." `.ai/memory/current-state.md` lists it as the next planned item.

Today, `app/embedding/client.py`'s `OllamaEmbeddingClient.embed` calls Ollama for every text every time, with no memoization. Two concrete cases pay for this unnecessarily:

- Re-ingesting the same PDF (or a PDF sharing chunks with one already ingested) re-embeds identical chunk text.
- `app/retrieval/service.py`'s `search` re-embeds a repeated query string on every call.

## Scope

In scope: a Redis-backed cache-aside layer in front of Ollama embedding calls, keyed by a stable hash of `(model, text)`, wired transparently into `OllamaEmbeddingClient` so no caller changes are needed. Graceful degradation when Redis is unreachable (always treated as a miss, never an error).

Out of scope: the retrieval/query-result cache and session/auth-token cache ADR-003 also names for Redis — separate follow-ups. Cache invalidation/eviction policy beyond a TTL. Re-embedding versioning (e.g. invalidating cache entries when a document is re-ingested with different chunking) — a TTL-only cache is accepted as sufficient since chunk text for a given `(model, text)` pair is deterministic input to a deterministic embedding call, so a stale cache entry is never wrong, only possibly outdated if the model changes without a version bump in `EMBEDDING_MODEL` (already covered, since the model name is part of the key).

## Module Layout

Following the existing `app/embedding/` structure, and touching nothing outside it:

- `app/embedding/config.py` — modified. `EmbeddingSettings` gains `redis_url: str` (default `redis://localhost:6379/0`) and `cache_ttl_seconds: int` (default `86400`, i.e. 24h), both overridable via the existing `EMBEDDING_` env prefix (`EMBEDDING_REDIS_URL`, `EMBEDDING_CACHE_TTL_SECONDS`).
- `app/embedding/cache.py` — new. `EmbeddingCache` (`Protocol` with `get(model: str, text: str) -> list[float] | None` and `set(model: str, text: str, vector: list[float]) -> None`) and `RedisEmbeddingCache(settings: EmbeddingSettings)` implementing it via the `redis` Python client. Key: `f"embedding:{sha256(f'{model}:{text}').hexdigest()}"`. Values are JSON-encoded vectors, written with `EX=cache_ttl_seconds`. Every Redis call is wrapped in `try/except redis.RedisError`, logged via `logger.exception`/`logger.warning`, and degrades to a miss (`get`) or a silent no-op (`set`) — Redis is cache-aside and must never be load-bearing, per ADR-003.
- `app/embedding/client.py` — modified. `OllamaEmbeddingClient.__init__` gains an optional `cache: EmbeddingCache | None = None` parameter, defaulting to `RedisEmbeddingCache(settings)`. `embed(texts)` looks up each text in the cache first; only the subset that misses is sent to Ollama in one batch call (preserving today's single-call-per-`embed()` behavior for the miss subset); results are written back to the cache and merged into the original input order before returning.

No changes to `app/embedding/service.py`, `app/ingestion/jobs.py`, or `app/retrieval/service.py` — both existing call sites already do `OllamaEmbeddingClient(settings)` with no `cache` argument, so they pick up caching automatically. This is deliberate: the cache lives inside the Ollama-backed client rather than as a decorator each call site has to opt into, because "transparent to callers" is a hard requirement here (both call sites are outside this ticket's scope to touch).

## Data Flow

`embed(texts)` → for each text, compute `(model, text)` → check `RedisEmbeddingCache.get` → texts with a hit keep their cached vector; texts that miss are collected, sent to Ollama in a single `client.embed(model, input=missing_texts)` call → each returned vector is written back via `RedisEmbeddingCache.set` (fire-and-forget; a `set` failure is logged and ignored, it just means a future miss) → the full vector list is reassembled in the original input order and returned.

If `texts` is empty, unchanged: no cache lookups, no Ollama call, `[]` returned (existing behavior preserved).

## Config

New `EmbeddingSettings` fields, both `EMBEDDING_`-prefixed like the existing four:

- `EMBEDDING_REDIS_URL` — default `redis://localhost:6379/0`.
- `EMBEDDING_CACHE_TTL_SECONDS` — default `86400` (24 hours). Long enough that a same-day re-ingest of the same document is a cache hit, short enough that a stuck/never-evicted key set isn't a real operational concern at this project's scale.

## Infra

`docker-compose.yml` gets a `redis` service (`redis:7-alpine`, port `6379`) for local dev, alongside the existing `postgres` service. CI's test job gets a matching `redis:7-alpine` service container (GitHub Actions `services:`), so `tests/embedding/test_cache.py` exercises a real Redis instead of a mock — mirroring ERP-011's explicit rejection of mocking the Postgres datastore in integration-shaped tests. No new CI step is needed beyond the service container itself (unlike Postgres, there's no schema/migration to apply — Redis is schemaless).

## Testing

- `app/embedding/cache.py`: `tests/embedding/test_cache.py` runs against a real Redis (local docker-compose / CI service container, default `redis://localhost:6379/0` overridden to a dedicated logical DB — `redis://localhost:6379/1` — via a `tests/embedding/conftest.py` fixture that also flushes that DB before/after each test, so cache tests never interact with the app's real db-0 data). Covers: miss returns `None`, set-then-get round-trips a vector, different models for the same text produce different keys (no collision), and a `RedisEmbeddingCache` pointed at an unreachable host degrades to a miss/no-op instead of raising.
- `app/embedding/client.py`: `tests/embedding/test_client.py` continues to mock Ollama (existing pattern) and now also injects a simple in-memory fake `EmbeddingCache` — keeping these tests fast and isolated, consistent with ERP-011's "embedding tests mock Ollama" precedent. New tests cover: a cache hit skips the Ollama call entirely, a partial hit only sends the miss subset to Ollama and reassembles results in order, and a cache miss writes the result back.
- `app/embedding/config.py`: `tests/embedding/test_config.py` extended with defaults + env-var-override assertions for the two new fields.
- The 90%-coverage CI gate (ERP-006) applies to all new/modified code.

## New Dependencies

Pre-approved by ADR-003 (Redis was already an accepted architectural decision, only its embedding-cache follow-up ticket was deferred) and by this ticket's brief:

- `redis` — the official Redis Python client. No other Redis library considered; it's the standard, actively-maintained choice and the project already has a "official client for the chosen backend" precedent (`ollama` for Ollama, `psycopg` for Postgres).

## Known Limitations

**No cache invalidation beyond TTL.** If a document is re-ingested with the same chunk text but the operator wants a fresh embedding (e.g. after an Ollama model weights update without changing `EMBEDDING_MODEL`'s name), the cache will still serve the old vector until the TTL expires. Accepted: the project has no versioning concept for "the same model name now means different weights" today, and the TTL bounds the staleness window.

**Cache and Ollama can disagree if Ollama's output for the same `(model, text)` pair is non-deterministic.** Nomic Embed via Ollama is deterministic for the same input/model in practice, but this isn't independently verified here — if that ever changes, the cache would silently pin the first-seen vector. Not treated as a real risk at this project's scale/model choice.
