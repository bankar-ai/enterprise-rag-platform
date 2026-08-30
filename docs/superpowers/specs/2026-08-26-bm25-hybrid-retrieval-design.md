# BM25 Hybrid Retrieval Design

Date: 2026-08-26

## Context

ERP-012 shipped `POST /retrieval/query` as vector-only (semantic) search: embed the query, FAISS nearest-neighbor search, hydrate from Postgres, rank by `1 / (1 + distance)`. Its design spec explicitly deferred BM25/hybrid retrieval as an additive follow-up. `current-state.md`'s "Next Planned Work" lists it first among three deferred retrieval enhancements (BM25 → reranking → PageIndex), each building on the last's output.

This spec covers BM25 hybrid retrieval: adding Postgres full-text search as a second retriever over chunk text, fused with today's vector results.

## Scope

In scope: a `tsvector` column + GIN index on `chunks`, a full-text search repository method, and RRF fusion of BM25 + vector ranks inside `service.search`. The endpoint contract (`RetrievalQuery`, `RetrievalResponse`, `RetrievedChunk`) is unchanged — this is a ranking-algorithm change inside the existing endpoint, not a new endpoint or new required fields.

Out of scope (deferred further): reranking (ERP-015), PageIndex-style structure-aware retrieval (ERP-016), per-document filtering, authentication, response caching, configurable fusion weights.

## Fusion Algorithm

Reciprocal Rank Fusion (RRF), `k=60` — see `.ai/adr/ADR-005.md` for the full research and justification. Summary: FAISS distances and Postgres `ts_rank` scores live on incomparable scales; RRF fuses purely on rank position (`1/(k+rank)` per retriever, summed), needing no score normalization or tunable weight.

## Schema Change

`chunks.search_vector`: a Postgres generated column (`GENERATED ALWAYS AS (to_tsvector('english', text)) STORED`), typed `TSVECTOR`, with a GIN index (`ix_chunks_search_vector`). Generated (not maintained by the application) so it can never drift from `text`. Modeled in SQLAlchemy via `sqlalchemy.dialects.postgresql.TSVECTOR` + `Computed(..., persisted=True)`, matching how `ChunkRecord`'s other columns are declared; a new Alembic migration adds the column and index without touching existing columns.

## Module Changes

- `app/ingestion/models.py` — `ChunkRecord` gains `search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed(...), nullable=True)`, not written to directly (the DB computes it).
- `alembic/versions/` — new migration: `op.add_column('chunks', sa.Column('search_vector', TSVECTOR, sa.Computed("to_tsvector('english', text)", persisted=True), nullable=True))` + `op.create_index(..., using='gin')`. Downgrade drops both.
- `app/ingestion/repository.py` — new `search_chunks_by_text(session, query_text, k) -> list[tuple[int, float]]`: runs `plainto_tsquery('english', query_text)` against `search_vector`, orders by `ts_rank(search_vector, query) DESC`, returns `(vector_id, rank)` pairs, nearest-first — same tuple shape as `FaissIndex.search` so `service.search` can treat both retrievers uniformly. Empty/no-match query returns `[]`.
- `app/retrieval/service.py` — `search` now also calls `search_chunks_by_text`, builds a rank-position list from each retriever's *order* (not raw score), computes RRF scores per `vector_id`, sorts descending, then hydrates from Postgres via the existing `get_chunks_by_vector_ids` (union of both retrievers' `vector_id`s). `RetrievedChunk.score` becomes the RRF score.
- `app/retrieval/schemas.py`, `app/retrieval/router.py` — unchanged.

## Data Flow

`POST /retrieval/query {"query": "...", "top_k": 5}` → `search()`: embed query → FAISS search (existing) → **new:** `search_chunks_by_text(session, query, top_k)` → **new:** RRF-fuse both rank-ordered lists by `vector_id` → take top `top_k` fused `vector_id`s → hydrate via `get_chunks_by_vector_ids` (called once, on the union of IDs from both retrievers, unchanged in shape) → return `RetrievedChunk`s ordered by fused RRF score. If FAISS has zero vectors and/or BM25 has zero matches, fusion runs on whatever retriever(s) did return hits — never raises for "no results," matching ERP-012's existing empty-index behavior. If *both* are empty, the endpoint still returns `200` with `results: []`.

## Testing

- `search_chunks_by_text`: tests run against real test Postgres (per ADR-003 / ERP-012 precedent) — exact term match ranks a chunk above a chunk without the term; empty/no-match query returns `[]`.
- RRF fusion: unit-tested directly (`_reciprocal_rank_fusion` or equivalent helper) with hand-computed expected scores for known rank lists, independent of DB/FAISS.
- `service.search`: extends ERP-012's existing test file — embedding mocked, FAISS and Postgres full-text search both run for real; covers vector-only-has-hits/BM25-empty, BM25-has-hits/vector-empty, both-have-hits-with-overlap, and both-empty.
- `router.py`: existing ERP-012 router tests must keep passing unmodified (contract stability); no new router tests are required beyond re-verifying the existing end-to-end ingestion→retrieval test still finds the chunk (now via hybrid ranking).
- 90% coverage gate applies to all new/modified modules.

## New Dependencies

None. Postgres full-text search is native; RRF fusion is plain Python.
