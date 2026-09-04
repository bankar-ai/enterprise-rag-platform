# Session — harden-embedding-cache

Date: 2026-09-04
Tickets Touched: ERP-022

## Decisions

- Ported ERP-021's three `RedisRetrievalCache` hardening fixes to `RedisEmbeddingCache` verbatim, matching the existing pattern exactly rather than inventing a new shape (same field name `redis_socket_timeout_seconds`, same `lru_cache`-wrapped factory shape, same `except (ValueError, UnicodeDecodeError)` guard).
- Item 2 from the ticket (cache-key scoping by user/tenant) intentionally left undone — no auth system exists yet to scope against, per the ticket's own acceptance criteria.

## Implementation Summary

- `app/embedding/config.py`: `EmbeddingSettings` gained `redis_socket_timeout_seconds: float = 2.0`.
- `app/embedding/cache.py`: `RedisEmbeddingCache.__init__` now passes `socket_connect_timeout`/`socket_timeout` into `redis.Redis.from_url(...)`; `get()`'s `json.loads(raw)` is now wrapped in `try/except (ValueError, UnicodeDecodeError)`, degrading to a logged miss instead of raising on a corrupt cache value; added `lru_cache`-wrapped `get_default_embedding_cache()` factory.
- `app/embedding/client.py`: `OllamaEmbeddingClient.__init__`'s default-cache path now calls `get_default_embedding_cache()` instead of constructing `RedisEmbeddingCache(settings)` inline, so the process reuses one Redis client/connection pool.
- `tests/embedding/test_cache.py`: added tests mirroring `tests/retrieval/test_cache.py` — socket timeouts reach the underlying client, a corrupt cached value degrades to a miss, and `get_default_embedding_cache()` is memoized.

## Verification

- `uv run ruff check app/embedding tests/embedding` — clean.
- `uv run mypy app/embedding` — clean.
- `uv run pytest tests/embedding -q` — 26 passed.
- `uv run pytest -q --cov=app --cov-fail-under=90` (full suite, real Postgres + Redis via `docker compose up -d postgres redis`) — 189 passed, `app/embedding/cache.py` at 100% coverage, 99.31% overall.

## Blockers

None.

## Next Steps

- ERP-022's item 2 (retrieval + embedding cache-key/scoping revisit) stays blocked on a future Authentication ticket.
- A session/auth-token cache remains the last unbuilt Redis use named in ADR-003.
- Consider requiring CI status checks in `main`'s branch protection now that CI gives real signal.
