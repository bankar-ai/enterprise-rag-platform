# Session — Per-Tenant FAISS Index Partitioning

Date: 2026-09-06
Tickets Touched: ERP-031

## Decisions

- Chose the simplest correct fix over a more sophisticated one, per explicit instruction: one physically separate FAISS index file per owner (`<index_dir>/<owner_id>.bin`), replacing the ERP-026 oversample-then-filter isolation mechanism entirely rather than layering more defenses on top of it.
- `filter_vector_ids_by_owner` (`app/ingestion/repository.py`, added during ERP-026's final review as the shared-index isolation fix) was deleted outright rather than kept as "defense in depth" -- once the index itself is physically partitioned, that function no longer does anything a bug could still slip past, and dead code that no longer guards anything real is not worth keeping.
- Reused the existing `FaissIndex` class unmodified as the per-owner index primitive; `OwnerFaissIndexStore` is a thin owner-keyed registry over it (lazy load/create, per-owner locking), not a reimplementation -- keeps the change minimal and testable.
- Kept the existing "load fresh, no cross-request caching" philosophy from the retrieval-endpoint design: `search()`/`embed_and_persist` build a fresh `OwnerFaissIndexStore` by default per call, so there's no new invalidation concern introduced.
- The single-index-to-per-owner migration is a standalone script (`app/embedding/migrate_to_per_owner.py`, run via `uv run python -m ...`), not an Alembic migration -- Alembic tracks Postgres schema only, and this step touches FAISS *data* files with no schema change at all.
- The migration script never deletes the legacy shared index file itself -- left as a manual operator step after the new per-owner indexes are confirmed correct, so a bad run can always be re-derived from the still-intact legacy file plus Postgres. It does delete a stale per-owner destination file before rewriting it, so re-running the script after a partial/failed run rebuilds cleanly instead of duplicating vectors.
- `EmbeddingSettings.faiss_index_path` renamed to `faiss_index_dir` (env var `EMBEDDING_FAISS_INDEX_DIR`) -- a directory root now, since it holds one file per owner instead of one shared file.

## Implementation Summary

- `app/embedding/index.py`: added `OwnerFaissIndexStore` (lazy per-owner `FaissIndex` load/create, per-owner `threading.Lock` for concurrent-ingestion safety, `add`/`search` keyed by `owner_id`, `path_for` for external path resolution) and `FaissIndex.reconstruct` (reads a vector back out by `vector_id`, used only by the migration script).
- `app/embedding/config.py`: `faiss_index_path` -> `faiss_index_dir`.
- `app/embedding/service.py`: `embed_and_persist`'s `faiss_index` param -> `faiss_index_store: OwnerFaissIndexStore | None`.
- `app/retrieval/service.py`: `search`'s `faiss_index` param -> `faiss_index_store: OwnerFaissIndexStore | None`; removed the `filter_vector_ids_by_owner` call and its import -- FAISS candidates are now owner-exclusive by construction. `RRF_OVERSAMPLE_MULTIPLIER` oversampling kept unchanged, now purely for RRF fusion quality.
- `app/ingestion/repository.py`: deleted `filter_vector_ids_by_owner`.
- `app/ingestion/jobs.py`: `run_ingestion_job`'s `faiss_index` param -> `faiss_index_store`.
- `app/embedding/migrate_to_per_owner.py` (new): one-off migration script + CLI (`uv run python -m app.embedding.migrate_to_per_owner [--legacy-index-path PATH] [--index-dir DIR]`); reads every `(vector_id, owner_id)` pair from Postgres, reconstructs each vector from the legacy shared index, writes it into the new per-owner store. Read-only against Postgres and the legacy index.
- `app/evaluation/runner.py`, `app/evaluation/generation_runner.py`: switched their self-managed temp-file-backed `FaissIndex` (never the real app's persisted index) to a temp-dir-backed `OwnerFaissIndexStore`, same isolation/cleanup lifecycle.
- Design spec: `docs/superpowers/specs/2026-09-06-per-tenant-faiss-index-design.md`.
- Tests: new `tests/embedding/test_migrate_to_per_owner.py` (redistribution correctness, legacy-index-untouched, safe-to-rerun); `tests/embedding/test_index.py` gained `OwnerFaissIndexStore` coverage (lazy creation, separate files per owner, structural isolation with no filtering step involved, empty-store search, reload-preserves-vectors, multi-call accumulation) plus a `FaissIndex.reconstruct` test. Updated for the renamed param/setting: `tests/embedding/test_config.py`, `test_service.py`, `tests/ingestion/test_jobs.py`, `test_router.py`, `tests/retrieval/test_service.py`, `test_router.py`, `test_telemetry.py`, `tests/evaluation/test_runner.py`, `test_generation_runner.py`. `tests/retrieval/test_service.py`'s old `test_search_filters_foreign_owner_vector_hits_before_truncating_to_top_k` (an ERP-026 regression test for the now-removed post-filter bug) was replaced with a stronger structural test (`test_search_never_returns_another_owners_vector_hit_regardless_of_raw_similarity`) plus a new file-level isolation test.

## Verification

- `uv run ruff check .` and `uv run mypy --strict app`: both clean.
- This sandbox's default Postgres/Redis ports (5432/6379) were occupied by another concurrent agent's containers; the first full-suite run against them produced 47 unrelated failures (auth, generation, evaluation tests) from cross-session data races (`ForeignKeyViolation`s from the other session's schema resets mid-run) -- not a real regression, but it made results unreadable. Reran against dedicated, isolated containers on ports 5433/6380 instead.
- Full suite: `uv run pytest -q --cov=app --cov-fail-under=90` -> **307 passed, 96% coverage**, with 2 pre-existing `tests/ingestion/test_parsers.py` docling-fallback tests deselected from that run (a Windows-native `access violation` crash under this sandbox's memory pressure from concurrent agents, same failure mode noted in ERP-030's session log) -- both pass individually in isolation (`6 passed`), confirmed not a regression from this change.
- Two real bugs found and fixed during this verification (not just environment noise):
  1. `FaissIndex.reconstruct` raised `RuntimeError: reconstruct not implemented for this type of index` -- the index was built as a plain `IndexIDMap`, which cannot translate an external id back to a reconstructable position. Fixed by switching to `IndexIDMap2`, which maintains that mapping.
  2. `migrate()` crashed with `key not found` when a chunk row's vector wasn't present in the (test-scoped) legacy index -- inevitable once other tests' unrelated chunks (sharing the same real test Postgres) were included in the same global query. Fixed by skipping such rows with a logged warning instead of aborting the whole migration, which is also more correct for real deployments given ERP-011's already-documented non-atomic Postgres/FAISS writes.
- Router-level end-to-end isolation test (`tests/retrieval/test_router.py::test_query_does_not_return_another_users_document`, from ERP-026) still passes unmodified against the new per-owner-index architecture.

## Blockers

Live-Ollama verification (re-running `uv run python -m app.evaluation.run` per the ticket's ask) was not possible -- no local Ollama instance is reachable in this sandboxed environment. Relied on the test suite above instead; noted as a deferred follow-up in `.ai/tickets/ERP-031.md`.

## Next Steps

- Whoever next has Ollama available locally should re-run the ERP-029 evaluation harness to confirm no Precision@k/Recall@k/MRR regression from this change.
- Operators with pre-ERP-031 ingested data should run `uv run python -m app.embedding.migrate_to_per_owner` before deploying this change, then manually remove the old `data/faiss_index.bin` once the new per-owner indexes are confirmed correct.
