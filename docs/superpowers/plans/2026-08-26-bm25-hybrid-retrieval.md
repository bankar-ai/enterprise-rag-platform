# BM25 Hybrid Retrieval Implementation Plan

**Goal:** Add Postgres full-text search (BM25-style, via `tsvector`/`ts_rank`) as a second retriever, fused with the existing FAISS vector retriever via Reciprocal Rank Fusion (RRF, `k=60`), inside the existing `POST /retrieval/query` endpoint — contract unchanged.

**Architecture:** New generated `tsvector` column + GIN index on `chunks` (migration + model change), a new full-text repository method mirroring `FaissIndex.search`'s `(vector_id, score)` shape, and an RRF fusion step inside `app/retrieval/service.py`.

**Tech Stack:** Postgres full-text search (native), SQLAlchemy `Computed` generated column, plain-Python RRF — no new dependencies.

## Global Constraints

Same as ERP-012's plan: `uv`-only deps (none needed here), no `print()`, mypy `--strict` on `app/`, 90% coverage gate, routes stay thin, RRF `k=60` per ADR-005, contract additive only.

---

### Task 1: Migration + model — `search_vector` column

- Modify `app/ingestion/models.py`: add `search_vector` (`TSVECTOR`, `Computed("to_tsvector('english', text)", persisted=True)`, nullable) to `ChunkRecord`.
- New Alembic migration (`uv run alembic revision -m "add search_vector tsvector column to chunks"`, then hand-edit `upgrade`/`downgrade` to add the computed column + GIN index, mirroring the existing migration's raw-`op.*` style).
- Verify: `uv run alembic upgrade head` against local Postgres; inspect `\d chunks` shows `search_vector` + GIN index; `Base.metadata.create_all` (used by `tests/conftest.py`) also produces the column since `Computed` is part of the model.
- Commit: `feat: add generated tsvector column and GIN index to chunks`

### Task 2: Repository — `search_chunks_by_text`

- Add to `app/ingestion/repository.py`: `search_chunks_by_text(session, query_text, k) -> list[tuple[int, float]]` using `func.plainto_tsquery('english', query_text)` and `func.ts_rank`, ordered rank-descending, limited to `k`, returning `(vector_id, rank)`. Empty `query_text` or no matches → `[]`.
- Tests in `tests/ingestion/test_repository.py`: persist chunks with distinguishable text, assert the chunk containing the query term ranks first; assert no-match query returns `[]`.
- Verify: `uv run pytest tests/ingestion/test_repository.py -v`
- Commit: `feat: add BM25 full-text search repository method`

### Task 3: RRF fusion helper

- Add a private `_reciprocal_rank_fusion(*ranked_id_lists: list[int], k: int = 60) -> list[tuple[int, float]]` to `app/retrieval/service.py`: for each input list (already rank-ordered), accumulate `1/(k+rank)` per `vector_id` (1-indexed rank) into a `dict[int, float]`, return `vector_id`s sorted by summed score descending.
- Unit tests in `tests/retrieval/test_service.py`: hand-computed RRF scores for two small known-order lists, including a case where one list is empty and a case with full overlap.
- Verify: `uv run pytest tests/retrieval/test_service.py -v -k rrf`
- Commit: `feat: add reciprocal rank fusion helper`

### Task 4: Wire fusion into `service.search`

- `service.search` now also calls `search_chunks_by_text(session, query, top_k)`, builds `vector_id` rank-order lists from both retrievers' outputs, fuses via `_reciprocal_rank_fusion`, takes the top `top_k` fused IDs, hydrates via `get_chunks_by_vector_ids` on the union of IDs actually needed, and sets `RetrievedChunk.score` to the fused RRF score. Preserve the existing "no vectors from embedding client" `ValueError` and orphaned-`vector_id` warning-and-drop behavior.
- Extend `tests/retrieval/test_service.py`: vector-hits/BM25-empty, BM25-hits/vector-empty, overlapping hits, both-empty (existing empty-index test still passes).
- Verify: `uv run pytest tests/retrieval/test_service.py -v`
- Commit: `feat: fuse BM25 and vector retrieval via RRF in search service`

### Task 5: Full verification + close-out

- Run full suite: `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90` (Postgres must be up: `docker compose up -d postgres`).
- Run `uv run ruff check .` and `uv run mypy app`.
- Confirm ERP-012's existing router tests (`tests/retrieval/test_router.py`) still pass unmodified.
- Update `.ai/tickets/ERP-014.md` to `Status: Done`, all criteria checked.
- Write `.ai/sessions/2026-08-26-bm25-hybrid-retrieval.md`.
- Update `.ai/memory/current-state.md`: move BM25 out of "Next Planned Work"/"What Does Not Exist Yet", add a summary bullet under "What Exists".
- Commit: `docs: close out ERP-014 ticket, session log, and current-state`
- Push branch, open PR into `develop` via `gh pr create` (do not merge).
