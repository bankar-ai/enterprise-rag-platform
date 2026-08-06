# Session — Embedding Generation & Persistence

Date: 2026-08-06
Tickets Touched: ERP-011

## Decisions

- Adopted the stack named in ADR-003 and `docs/architecture.md`: SQLAlchemy 2.0 + Alembic + `psycopg[binary]` for Postgres, `ollama` Python client for Nomic Embed embeddings, `faiss-cpu` for the vector index persisted to local disk. All five new dependencies approved up front (`e197a1b`).
- Two new modules: `app/core/` (shared DB engine/session, config) and `app/embedding/` (Ollama client, FAISS wrapper, `embed_and_persist` orchestration service). `app/ingestion/models.py` and `app/ingestion/repository.py` added for Postgres persistence of documents/chunks.
- `run_ingestion_job` (`app/ingestion/jobs.py`) extended to call `embed_and_persist` after the existing parse+chunk step, inside the same try/except, so a job failure at the embedding/persistence stage is caught, logged, and reported as `FAILED` exactly like a parse/chunk failure — no new failure-handling path was invented.
- Test strategy: embedding-client tests mock Ollama; repository/DB tests run against a real Postgres (docker-compose locally, GitHub Actions service container in CI) — SQLite was explicitly rejected as a stand-in per ADR-003.
- Redis embedding cache (also named in ADR-003) was explicitly deferred to a follow-up ticket, per ERP-011's own Notes — it's cache-aside and never load-bearing, so adding it later changes nothing about correctness, only latency.

## Implementation Summary

Eleven implementation tasks, all reviewed clean or resolved, landed on `erp-011-embedding-persistence` (commits `44fdb8e..f957da5`):

1. `e197a1b` — added `sqlalchemy`, `alembic`, `psycopg[binary]`, `ollama`, `faiss-cpu` dependencies.
2. `82e4b4f` — database settings and a lazily-constructed session factory.
3. `a9b32c7` — `DocumentRecord` and `ChunkRecord` SQLAlchemy models.
4. `8d9abee` — Alembic migration environment and the initial schema migration.
5. `05b6f39` → `7e4c425` → `1bdb1cf` — `app/ingestion/repository.py` persisting a document and its chunks in one transaction (see Decisions below on the false start here).
6. `8a0a607` — embedding settings.
7. `15714bb` — Ollama-backed embedding client.
8. `f1691e3` — FAISS index wrapper (load/add/save, disk-persisted).
9. `752d072` — `embed_and_persist` orchestration service tying the embedding client, FAISS index, and repository together.
10. `b29c685` — wired `embed_and_persist` into `run_ingestion_job` so a `DONE` job means chunks are embedded and durably persisted, not just held in memory.
11. `f957da5` — `docker-compose.yml` Postgres service for local dev, and CI extended to run tests against a real Postgres service container (plus a migration check).

Net effect: a `DONE` ingestion job now means the source document and its chunks are stored in Postgres, and their embeddings (Nomic Embed via Ollama) are stored in a FAISS index persisted to local disk — durable across process restarts. Previously this stopped at in-memory chunks.

## Blockers

Two real discovery points during implementation, worth recording honestly rather than smoothing over:

- **Task 5 (repository persistence) — a reviewer claim turned out to be empirically wrong.** A reviewer argued that SQLAlchemy's column-level foreign key alone is sufficient to guarantee insert ordering (document before its chunks) without an ORM-level `relationship()`, and that the intermediate `session.flush()` between inserting the document and inserting its chunks was therefore unnecessary (`7e4c425`). Removing it caused a real `ForeignKeyViolation` in the test suite — chunks were being flushed before the document they reference existed. The controller resolved this directly by reverting to the original two-flush implementation and declining the `relationship()`-only approach (`1bdb1cf`). Lesson: SQLAlchemy ORM flush/insert ordering across related rows is not guaranteed by column-level FK constraints alone in the absence of `relationship()`-driven dependency resolution — this was a plausible-sounding theoretical claim from several models that didn't hold up under an actual test run. Treat "insert-order" claims about the ORM as needing an empirical check, not just a read of the SQLAlchemy docs.
- **Task 10 (wiring embedding into the job pipeline) — surfaced a plan gap in pre-existing tests.** Extending `run_ingestion_job` to call `embed_and_persist` broke two pre-existing test suites that hadn't been touched by the plan: `test_jobs.py`'s success-path test, and independently `test_router.py`'s two end-to-end tests — both started attempting live Ollama calls because the new code path now runs unconditionally after chunking. Neither failure was anticipated by the task breakdown. Fixed with test-only changes: fake embedding clients injected in `test_jobs.py`, and a monkeypatched `OllamaEmbeddingClient.embed` plus a redirected FAISS index path in `test_router.py`. No production defaulting or fallback behavior was weakened to make tests pass — the fixes are entirely on the test side.

No unresolved blockers remain; ERP-011 is fully complete.

## Next Steps

- Redis embedding cache (deferred from this ticket, per ADR-003 and ERP-011's Notes) — a follow-up ticket to add cache-aside embedding lookups ahead of Ollama calls. Non-load-bearing, so it can land whenever convenient.
- Retrieval/query endpoint — the next vertical slice: read persisted chunks/vectors back out (similarity search against the FAISS index, chunk text from Postgres). Currently there is still no retrieval or generation code in the repository at all.
- Consider requiring CI status checks in `main`'s branch protection now that CI covers ruff, mypy, pytest+coverage, gitleaks, and (as of this ticket) a real Postgres service container — still not configured.
