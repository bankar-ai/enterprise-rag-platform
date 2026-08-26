# Session — Redis Embedding Cache

Date: 2026-08-26
Tickets Touched: ERP-013

## Decisions

- Implemented the Redis cache-aside layer named in ADR-003 and deferred from ERP-011: a cache-aside lookup in front of Ollama embedding calls, keyed by a sha256 hash of `(model, text)` so the same text under a different configured model never collides.
- Wired the caching **inside `OllamaEmbeddingClient` itself** rather than as a decorator each call site opts into. Both existing callers — `app/embedding/service.py`'s `embed_and_persist` (via `app/ingestion/jobs.py`) and `app/retrieval/service.py`'s `search` — already construct `OllamaEmbeddingClient(settings)` directly, and the task explicitly forbade touching `app/retrieval/` or `app/ingestion/`. Baking the cache into the client's `__init__`/`embed` (new optional trailing `cache` param, defaulting to `RedisEmbeddingCache`) means both callers get caching for free with zero changes to either module — verified by reading both files before implementing, not just assumed.
- `RedisEmbeddingCache` treats every Redis failure as a miss (`get`) or silent no-op (`set`), never raising — ADR-003 is explicit that Redis must never be load-bearing. Covered by a dedicated test pointing at an unreachable Redis URL.
- Test strategy mirrors ERP-011's Postgres precedent: `tests/embedding/test_cache.py` runs against a real Redis (no mocking the datastore), using a dedicated logical DB (`redis://localhost:6379/1`, set via a new `tests/embedding/conftest.py` fixture that flushes it around every test) so cache tests never touch the app's default db-0 data. `tests/embedding/test_client.py` keeps mocking Ollama and now also injects a simple in-memory fake `EmbeddingCache`, consistent with the existing "client tests fake the external call" pattern.
- `redis` (official Python client) was the only dependency added — pre-approved via ADR-03, no other Redis library considered.

## Implementation Summary

Six commits on `erp-013-redis-embedding-cache`:

1. `7169aa5` — added the `redis` dependency; created `.ai/tickets/ERP-013.md`, the design spec (`docs/superpowers/specs/2026-08-26-redis-embedding-cache-design.md`), and the implementation plan (`docs/superpowers/plans/2026-08-26-redis-embedding-cache.md`).
2. `375dbc0` — `EmbeddingSettings` gained `redis_url` (default `redis://localhost:6379/0`) and `cache_ttl_seconds` (default `86400`), both `EMBEDDING_`-prefixed like the existing four fields.
3. `161a8d9` — `app/embedding/cache.py`: `EmbeddingCache` protocol + `RedisEmbeddingCache`, keyed by `embedding:{sha256(model:text)}`, TTL-bounded, resilient to Redis errors. New `tests/embedding/conftest.py` fixture for a real-Redis test DB.
4. `c787878` — `OllamaEmbeddingClient` does cache-aside lookups internally: per-text cache check, only cache misses batched to Ollama in one call, results written back, original order preserved. `tests/embedding/test_client.py` extended with hit/partial-hit/miss/default-cache-type coverage using an injected fake cache.
5. `8501726` — `docker-compose.yml` gained a `redis:7-alpine` service; `.github/workflows/ci.yml` gained a matching Redis service container alongside the existing Postgres one.
6. This commit — ticket, session log, and `current-state.md` closed out.

Net effect: `OllamaEmbeddingClient.embed()` now checks Redis before calling Ollama for every text, batches only the misses, and writes results back — transparently to both existing callers. No interface change visible outside `app/embedding/`.

## Blockers

None specific to the implementation. One environmental note worth recording: this worktree shares a single Postgres container (port 5432) with the sibling `erp-014-retrieval-enhancements` worktree running in parallel. A single `tests/embedding/test_service.py` run hit a transient `UniqueViolation` from a stale row, reproducing cleanly as a pass when that test file was re-run in isolation immediately after — confirmed as a concurrent-test-run collision on shared infra, not a defect in this ticket's code, before moving on. Full-suite coverage run (83 tests, 99.34% coverage) was green.

## Next Steps

- BM25/hybrid retrieval, reranking, and PageIndex-style retrieval remain deferred from ERP-012 (untouched by this ticket).
- ADR-003 also names a retrieval/query-result cache (short TTL) and a session/auth-token cache as future Redis uses on the same instance — separate follow-up tickets, not covered here.
- Consider requiring CI status checks in `main`'s branch protection now that CI covers ruff, mypy, pytest+coverage, gitleaks, a real Postgres service container, and (as of this ticket) a real Redis service container — still not configured.
