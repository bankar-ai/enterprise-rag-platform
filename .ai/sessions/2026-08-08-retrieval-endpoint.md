# Session — Retrieval Endpoint (ERP-012)

Date: 2026-08-08
Tickets Touched: ERP-012

## Decisions

- Scoped this slice to synchronous, vector-only (semantic) search: `POST /retrieval/query` embeds the query text, searches the FAISS index built by ERP-011, hydrates matching chunks from Postgres, and returns them ranked by similarity. BM25/hybrid retrieval, reranking, and PageIndex-style structure-aware retrieval were all considered and deliberately deferred as additive follow-ups rather than folded into this endpoint's contract:
  - **BM25** would use Postgres's built-in full-text search (`tsvector`) over chunk text already stored — no new dependency, fits the project's "avoid dependency bloat" rule — and becomes a second retriever fused with today's vector results without changing today's contract.
  - **Reranking** is a post-processing step over whatever candidates retrieval already returns, so it composes on top of this slice.
  - **PageIndex-style retrieval** is an alternative traversal strategy that would use the `section_path` metadata chunks already carry from ingestion, so it doesn't require today's chunk storage to change.
- Similarity score defined as `score = 1 / (1 + distance)`, so higher is always better and the score is bounded to `(0, 1]` — keeps the response format monotonic and easy to reason about regardless of the underlying FAISS distance metric.
- `service.search` constructs a fresh `FaissIndex` per request (no cross-request in-memory caching) — same "load fresh from disk" approach `embed_and_persist` already uses in ERP-011, trading some per-request latency for always seeing the latest saved index and avoiding a new cache-invalidation concern.
- An empty FAISS index (nothing ingested yet) returns `200` with `results: []`, not an error — "no matches because nothing exists yet" is a normal state, not a failure.
- A FAISS hit whose `vector_id` has no matching Postgres row is silently dropped from the response rather than raising, since a stale/orphaned vector shouldn't break a query for everything else (this can only happen because Postgres persistence and FAISS writes aren't atomic, a limitation ERP-011 already documented).

## Implementation Summary

New `app/retrieval/` module, parallel to `app/ingestion/` and `app/embedding/`:
- `schemas.py` — `RetrievalQuery` (`query: str`, `top_k: int`), `RetrievedChunk` (chunk text + full provenance + `score`), `RetrievalResponse` (`results: list[RetrievedChunk]`).
- `service.py` — `search(query, top_k, settings, embedding_client, faiss_index, session)`: embeds the query, calls `FaissIndex.search`, hydrates hits via `get_chunks_by_vector_ids`, and returns results in FAISS's nearest-first order.
- `router.py` — `POST /retrieval/query`, validation only, no business logic in the route.

Two additions to existing modules:
- `app/embedding/index.py`: `FaissIndex.search(vector, k) -> list[tuple[int, float]]`, nearest-first, backed by `faiss.IndexIDMap`'s own `.search()`.
- `app/ingestion/repository.py`: `get_chunks_by_vector_ids(session, vector_ids) -> dict[int, ChunkRecord]`, a single-query lookup keyed by `vector_id`.

Request validation: `top_k` defaults to 5, bounded `1–50` (`Field(ge=1, le=50)`); `query` requires `min_length=1`, so an empty query is rejected with `422` before any embedding call is made. `retrieval_router` is registered in `app/main.py`.

Five tasks landed as five commits on `erp-012-retrieval-endpoint`, each reviewed clean:
- `07d0f3c` feat: add nearest-neighbor search to FaissIndex
- `91a7de5` feat: add vector-id lookup for hydrating FAISS search hits
- `94462ae` feat: add retrieval request/response schemas
- `e346575` feat: add semantic search service
- `3cae58e` feat: add POST /retrieval/query endpoint

Per the plan's testing strategy: `FaissIndex.search` tested against a temp-directory index; `get_chunks_by_vector_ids` tested against the real test Postgres (per ADR-003); `service.search` tests mock the embedding call but run FAISS search and the Postgres lookup for real, covering normal results, an empty index, and `top_k` larger than the number of ingested chunks; `router.py` has an end-to-end test through `POST /retrieval/query` with the embedding call stubbed. All new/modified modules meet the existing 90% coverage gate. No new dependencies were introduced.

## Blockers

None outstanding, but two findings from the task-by-task review are worth recording rather than glossing over:

- **Task 4 — untested orphaned-`vector_id` path.** The code path that silently drops a FAISS hit with no matching Postgres row is implemented (per the design's documented behavior) but has no dedicated test exercising it. This is a pre-existing gap in the plan's own test list, not something introduced during implementation — flagged as a deferred Minor finding in `progress.md` (`Task 4: minor (deferred): orphaned-vector_id drop path untested (pre-existing gap in brief, low risk)`). Low risk because the condition requires the two stores to already be out of sync, which is itself rare and already a known limitation from ERP-011.
- **Task 5 — cross-directory fixture sharing doesn't work in pytest.** The brief assumed `simple_text_pdf` (a fixture originally defined in `tests/ingestion/conftest.py`) would be automatically visible to `tests/retrieval/test_router.py`. It isn't: pytest only shares fixtures with descendants of the directory a `conftest.py` lives in, not with sibling test directories. After reproducing the failure, the fixture was relocated to the root `tests/conftest.py`, with no duplication and no side effects on the existing ingestion tests that used it. Worth carrying forward as a general lesson (pytest conftest fixture scoping is ancestor-only, not sibling-shared) — similar in spirit to the SQLAlchemy flush-ordering lesson recorded in the ERP-011 session log.

## Next Steps

This branch (`erp-012-retrieval-endpoint`) is not yet merged to `develop`/`main`. Candidate follow-ups, in no fixed priority order — whichever the user prioritizes next:

- BM25 retrieval via Postgres full-text search (`tsvector`), fused with today's vector results as a second retriever.
- Reranking as a post-processing step over retrieval candidates.
- PageIndex-style structure-aware retrieval using the `section_path` metadata chunks already carry.
- Redis embedding cache (still deferred from ERP-011) — cache-aside lookups ahead of Ollama calls.
- A dedicated test for Task 4's orphaned-`vector_id` drop path, closing the gap noted above.
