# Session — Retrieval Query Cache

Date: 2026-09-04
Tickets Touched: ERP-021, ERP-022

## Decisions

Scoped ERP-021 to only the retrieval/query-result cache named in ADR-003 — the session/auth-token cache (ADR-003's third named Redis use) stays explicitly out of scope, since no auth system exists yet to define what a session/token is. Cache key hashes all four `search()` parameters that affect its output (`query`, `top_k`, `rerank`, `expand_sections`), keyed on the full pipeline result rather than just the base retrieval, so a cache hit is always byte-correct for exactly what was asked. Wired inside `search()` itself (mirroring ERP-013's embedding-cache pattern) rather than at each call site, so both `app/retrieval/router.py` and `app/generation/service.py` get caching transparently. Built via subagent-driven-development (4 tasks, each with its own dispatched implementer + reviewer) in an isolated worktree.

Final whole-branch review found 3 Important findings (no Redis socket timeouts, a fresh connection pool built per `search()` call instead of memoized, and cache-value deserialization sitting outside the Redis-error guard so a corrupt entry could raise instead of degrading to a miss) despite an overall "ready to merge: yes" verdict — ruled to fix all three anyway rather than let the "yes" verdict wave them through, since the loop's rule is to act on Important findings. Fixed and re-reviewed clean, scoped only to this branch's own files (`app/retrieval/cache.py`, `config.py`, `service.py`) — the identical gaps in ERP-013's `app/embedding/cache.py` were explicitly left alone and spun out into a new follow-up ticket, ERP-022, rather than expanding this branch's scope.

## Implementation Summary

- `app/retrieval/config.py`: `RetrievalSettings` (`RETRIEVAL_`-prefixed `redis_url`, `cache_ttl_seconds=300`, `redis_socket_timeout_seconds=2.0`), `get_retrieval_settings()`.
- `app/retrieval/cache.py` (new): `RetrievalCache` protocol + `RedisRetrievalCache`, plus `get_default_retrieval_cache()` (`lru_cache`-wrapped, added during the final-review fix wave to stop building a new connection pool per call).
- `app/retrieval/service.py`: `_cache_key(query, top_k, rerank, expand_sections)`, and `search()` gained a `cache: RetrievalCache | None = None` parameter — checks cache first, returns on hit (skipping the entire pipeline), populates on every miss path including empty results.
- `tests/retrieval/conftest.py` (new): points Redis at a dedicated test-only logical DB (2, distinct from the embedding cache's DB 1) with an autouse flush fixture.
- Spec: `docs/superpowers/specs/2026-09-04-retrieval-query-cache-design.md`. Plan: `docs/superpowers/plans/2026-09-04-retrieval-query-cache.md`.
- Full suite: 186 tests, 99.30% coverage, mypy/ruff clean.
- Merged to `develop` via PR #18 (merge commit `476e65c`).
- Filed ERP-022 (Backlog) for the deferred embedding-cache hardening + auth-era cache-key revisit.

## Blockers

None.

## Next Steps

Promote ERP-021 to `main` in a future `develop` → `main` PR (following the PR #6/#11/#15/#17 pattern) whenever the next promotion happens. ERP-022 is Backlog and unscheduled. Remaining `docs/roadmap.md` gaps: DOCX/PPTX ingestion (explicitly deferred per standing user direction), Authentication, Evaluation, Observability, and a real deployment story beyond local-dev `docker-compose.yml`.
