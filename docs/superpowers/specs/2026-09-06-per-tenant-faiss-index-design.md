# Design — Per-Tenant FAISS Index Partitioning (ERP-031)

Date: 2026-09-06

## Problem

Today (ERP-011 onward) there is one shared FAISS index for every owner. `app/retrieval/service.py`'s
`search()` oversamples `top_k * RRF_OVERSAMPLE_MULTIPLIER` candidates from the shared index, then
filters to the caller's own documents (`filter_vector_ids_by_owner`, added as a bug fix during
ERP-026's final review) before RRF fusion. This is correctness-fragile: a caller's true top-k vector
matches can still be pushed out of the oversampled window entirely by other tenants' vectors --
there's no bound on this other than guessing at a larger oversample multiplier, and no multiplier is
truly safe under an adversarial or just large-enough number of other tenants' documents.

## Approach

Partition the vector index per owner: one on-disk FAISS index per `owner_id`, instead of one shared
index with post-hoc filtering. A caller's search physically cannot return another owner's vectors,
because another owner's vectors are never in the file being searched.

### Index file layout

New `app/embedding/index.py`'s `OwnerFaissIndexStore`:

- One index file per owner: `<index_dir>/<owner_id>.bin` (`owner_id` is a UUID, already a stable,
  collision-free, filesystem-safe key).
- `EmbeddingSettings.faiss_index_dir` (renamed from `faiss_index_path`, default
  `"data/faiss_index"`) is the directory root; `OwnerFaissIndexStore(index_dir, dimension)` derives
  each owner's path from it. `EMBEDDING_FAISS_INDEX_DIR` is the corresponding env var.
- Reuses the existing `FaissIndex` class unmodified as the single-index primitive (lazy
  load-or-create at a path, add, save, search) -- `OwnerFaissIndexStore` is a thin owner-keyed
  registry around it, not a reimplementation.
- Lazy creation: an owner's index file is created on first `add()` for that owner (via
  `FaissIndex`'s existing load-or-create-empty behavior); lazy load: first `search()` or `add()` for
  an owner loads its file into memory if not already cached in the store instance.
- A per-owner `threading.Lock` (created on first access, guarded by a registry lock) serializes
  `add`/`save` for a given owner, since ingestion jobs run on background threads
  (`app/ingestion/jobs.py`) and two concurrent ingestions for the *same* owner must not race on the
  same in-memory FAISS object or interleave writes to the same file. Different owners never
  contend -- there's no shared lock across owners, which is the whole point.
- Matches the existing "load fresh, no cross-request caching" philosophy from
  `docs/superpowers/specs/2026-08-08-retrieval-endpoint-design.md`: `search()` and `embed_and_persist`
  each build a fresh `OwnerFaissIndexStore` by default (no process-lifetime instance), so there's no
  new invalidation concern -- a store's internal per-owner cache only matters within one call's
  lifetime (or, for the evaluation runners, within one run).

### Why this scales without a rewrite later

`owner_id -> file path` is already the sharding key. Splitting owners across multiple hosts/processes
later (e.g. consistent-hashing owners to N index shards, or moving cold owners' index files to
cheaper/slower storage) only requires changing how `OwnerFaissIndexStore` resolves a path for a given
`owner_id` -- e.g. a shard-aware subclass or a lookup service in front of `_path_for` -- not touching
any caller (`embed_and_persist`, `search`). No caller ever assumes all indexes live on one filesystem
or in one process; they only call `store.add(owner_id, ...)` / `store.search(owner_id, ...)`.

### `embed_and_persist` (`app/embedding/service.py`)

`faiss_index: FaissIndex | None` becomes `faiss_index_store: OwnerFaissIndexStore | None`, defaulting
to `OwnerFaissIndexStore(settings.faiss_index_dir, settings.dimension)`. `store.add(owner_id,
vector_ids, vectors)` both adds and persists (mirrors the old inline `faiss_index.add(...)` +
`faiss_index.save()`).

### `search()` (`app/retrieval/service.py`)

`faiss_index: FaissIndex | None` becomes `faiss_index_store: OwnerFaissIndexStore | None`, defaulting
the same way. `vector_hits = faiss_index_store.search(owner_id, vectors[0], candidate_k)` replaces
the shared-index search. Because the index itself is owner-scoped, `filter_vector_ids_by_owner`
(`app/ingestion/repository.py`) is no longer needed for correctness and is removed along with its
call site -- keeping it around as inert "defense in depth" would be dead weight that duplicates what
the index's physical partitioning already guarantees, and per `CLAUDE.md`'s dependency/complexity
guidance, code that no longer does anything real should go, not linger.

`RRF_OVERSAMPLE_MULTIPLIER`-based oversampling (4x `top_k` per retriever before fusion) is kept
unchanged -- it now exists purely for RRF fusion quality (letting a chunk that ranks just outside
`top_k` on one signal still fuse in via the other), which was always its secondary purpose alongside
isolation; isolation no longer depends on it at all.

`get_chunks_by_vector_ids(session, vector_ids, owner_id)` keeps its `owner_id` filter at hydration
time. This isn't "post-hoc isolation" in the old sense (the FAISS candidates are already
owner-exclusive) -- it's simply how chunk text gets fetched, and it remains a legitimate second,
structurally-independent check: even if a bug ever let a foreign `vector_id` into the FAISS hit list,
this join would still refuse to hydrate it.

### Evaluation runners (`app/evaluation/runner.py`, `generation_runner.py`)

Both build a dedicated non-shared index (never the real app's) when nothing is injected. Previously:
one temp *file*. Now: a temp *directory*, holding one `OwnerFaissIndexStore` rooted there, used for
both `embed_and_persist` and `search` across the run (both today only ever use one eval owner, but
the store handles any number transparently). Injectable `faiss_index_store` param, same shape as
`search`/`embed_and_persist`.

### Migration for existing single-index data

There is pre-ERP-031 data: chunks already persisted in Postgres with `vector_id`s pointing into one
shared `data/faiss_index.bin`. This isn't Postgres-managed, so it isn't an Alembic migration --
Alembic only tracks schema, and this is purely a FAISS *data* file redistribution with no schema
change (`ChunkRecord.vector_id` and `DocumentRecord.owner_id` are unchanged). A dedicated one-off
script instead: `app/embedding/migrate_to_per_owner.py`, run via
`uv run python -m app.embedding.migrate_to_per_owner --legacy-index-path data/faiss_index.bin`.

Approach: open the legacy shared index read-only, join `ChunkRecord` to `DocumentRecord` in Postgres
to get every `(vector_id, owner_id)` pair, reconstruct each vector from the legacy index by its
`vector_id` (via `FaissIndex.reconstruct`, which requires the index to be an `IndexIDMap2` --
`IndexIDMap` alone cannot translate an external id back to a reconstructable position), and write it
into the new `OwnerFaissIndexStore`-managed per-owner index for that `owner_id`. A chunk row with no
matching vector in the legacy index (possible given ERP-011's documented non-atomic Postgres/FAISS
writes) is skipped with a logged warning rather than aborting the run. Idempotent-safe to re-run: it
always creates full per-owner indexes from Postgres + the legacy index rather than incrementally
patching, so re-running after a partial/failed run just rebuilds the same per-owner files (it does
not delete the legacy file itself -- that's left for the operator to remove once the per-owner
indexes are confirmed correct, documented in the script's own module docstring and CLI `--help`).
Never touches Postgres -- read-only there.

### What does not change

Hybrid fusion (RRF), reranking (ERP-015), and section-expansion (ERP-016) are untouched -- they
operate purely on already-hydrated, already-owner-scoped `RetrievedChunk` lists, with no dependency
on how the vector candidates were isolated. `POST /retrieval/query`'s request/response contract is
unchanged.

## Test changes

- `tests/embedding/test_index.py`: new `OwnerFaissIndexStore` tests -- lazy per-owner creation,
  physical isolation (owner A's `search` never returns owner B's vectors even when both are added to
  the same store instance), separate files on disk, empty-store search returns `[]` without creating
  a file.
- `tests/retrieval/test_service.py`: every `FaissIndex(...)`-per-test-file construction becomes an
  `OwnerFaissIndexStore(str(tmp_path), dimension=4)` (one store, keyed internally by owner, shared
  across owners within a test where relevant); `faiss_index=` becomes `faiss_index_store=`. The old
  `test_search_filters_foreign_owner_vector_hits_before_truncating_to_top_k` (a regression test for
  the ERP-026 post-filter-ordering bug, now structurally obsolete) is replaced by a test asserting
  the *stronger* property the ticket asks for: three owners share one `OwnerFaissIndexStore`, and a
  search restricted to one owner returns only that owner's chunk regardless of the other owners' raw
  vector similarity ranking -- with no filtering step involved at all, because the other owners'
  vectors are never in the searched index in the first place. The orphaned-FAISS-hit-with-no-Postgres-row
  regression test drops its `filter_vector_ids_by_owner` monkeypatch entirely (that function no longer
  exists) since the hydration-drop path it targets is now reached directly, with no owner-filter step
  in between.
- `tests/retrieval/test_router.py`, `tests/ingestion/test_router.py`: `EMBEDDING_FAISS_INDEX_PATH` env
  var becomes `EMBEDDING_FAISS_INDEX_DIR`. `tests/retrieval/test_router.py`'s existing
  `test_query_does_not_return_another_users_document` (added in ERP-026) continues to pass and now
  exercises real physical partitioning end-to-end rather than the old post-filter.
- `tests/embedding/test_service.py`, `tests/ingestion/test_jobs.py`, `tests/evaluation/test_runner.py`,
  `tests/evaluation/test_generation_runner.py`, `tests/embedding/test_config.py`,
  `tests/retrieval/test_telemetry.py`: mechanical updates for the renamed parameter/setting.
- New migration script test: `tests/embedding/test_migrate_to_per_owner.py` -- seeds a legacy shared
  index + Postgres chunks across two owners, runs the migration, asserts each owner's new per-owner
  index contains exactly their own vectors (reconstructed correctly) and the legacy index is
  untouched.

## Deferred / Future Follow-ups

- Sharding owners across multiple hosts/processes -- the design supports it (see above) but isn't
  built now; revisit once there's a concrete scale trigger.
- Deleting the legacy shared index file automatically as part of the migration script -- left as a
  manual operator step so a bad migration run can always be re-derived from the still-intact legacy
  file plus Postgres.
- Compacting/rebuilding a single owner's index (e.g. after many deletes) is out of scope -- no
  chunk-deletion feature exists yet at all.
