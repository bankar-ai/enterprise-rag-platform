# Retrieval Endpoint Design

Date: 2026-08-08

## Context

ERP-011 made ingestion durable: chunks are embedded (Nomic Embed via Ollama), persisted to Postgres, and their vectors added to a local FAISS index. Nothing yet reads any of that back out — `current-state.md`: "No retrieval or generation code at all yet." `docs/roadmap.md` and `docs/architecture.md` both name a broader eventual retrieval story (Hybrid Retrieval, PageIndex-inspired retrieval, BM25, reranking), but none of that infrastructure exists yet, and building it all at once was considered and rejected during brainstorming in favor of the same incremental-vertical-slice approach used for ingestion and ERP-011.

This spec covers the first retrieval slice: a synchronous semantic (vector-only) search endpoint. It is scoped so later additions are additive, not a redesign:

- **BM25** would most naturally use Postgres's built-in full-text search (`tsvector`) over the chunk text already stored — no new service, fits the project's "avoid dependency bloat" rule. It becomes a second retriever fused with today's vector results; today's endpoint contract doesn't change.
- **Reranking** is a post-processing step over whatever candidates retrieval already returns — composes on top of this slice rather than requiring rework of it.
- **PageIndex-style retrieval** is an alternative traversal strategy that would use the `section_path` metadata chunks already carry (from the ingestion slice) — doesn't require today's chunk storage to change.

## Scope

In scope: a single `POST /retrieval/query` endpoint that embeds a query, searches the FAISS index, hydrates matching chunks from Postgres, and returns them ranked by similarity.

Out of scope (explicitly deferred): BM25/hybrid retrieval, reranking, PageIndex-style structure-aware retrieval, per-document filtering, authentication, response caching, and any in-memory caching of the FAISS index across requests.

## Module Layout

New `app/retrieval/` module, parallel to the existing `app/ingestion/` and `app/embedding/`:

- `schemas.py` — `RetrievalQuery` (`query: str`, `top_k: int = 5`), `RetrievedChunk` (chunk text + full provenance + `score: float`), `RetrievalResponse` (`results: list[RetrievedChunk]`).
- `service.py` — `search(query, top_k, settings, embedding_client, faiss_index, session) -> list[RetrievedChunk]`: embeds the query text, searches the FAISS index for the nearest `top_k` vectors, fetches the corresponding rows from Postgres, and returns them in similarity order. `embedding_client`/`faiss_index`/`settings` are injectable (same pattern as `app/embedding/service.py`'s `embed_and_persist`), defaulting to real implementations when not supplied — so tests can inject fakes without a live Ollama server.
- `router.py` — `POST /retrieval/query`, validates the request and calls `service.search`; no business logic in the route itself, per the existing routes/service-layer principle.

Two small additions to already-existing modules, not new files:

- `app/embedding/index.py`'s `FaissIndex` gains `search(vector: list[float], k: int) -> list[tuple[int, float]]`, returning `(vector_id, distance)` pairs ordered nearest-first. Backed by `faiss.IndexIDMap`'s own `.search()`.
- `app/ingestion/repository.py` gains `get_chunks_by_vector_ids(session: Session, vector_ids: list[int]) -> dict[int, ChunkRecord]`, a lookup keyed by `vector_id` so `service.search` can re-associate each FAISS hit with its full chunk row.

## Data Flow

`POST /retrieval/query {"query": "...", "top_k": 5}` → router validates the request body → `search()`: embed the query text (`EmbeddingClient.embed([query])`, taking the single resulting vector) → `FaissIndex.search(vector, top_k)` returns up to `top_k` `(vector_id, distance)` pairs → `get_chunks_by_vector_ids` fetches the matching `ChunkRecord` rows in one query → each pair is zipped with its chunk and converted to a `RetrievedChunk` (`score = 1 / (1 + distance)`, so higher is always better and the score is bounded to `(0, 1]`) → results returned in FAISS's nearest-first order (ties in distance broken by FAISS's own order, not re-sorted).

If the FAISS index has zero vectors (nothing ingested yet), `FaissIndex.search` returns an empty list and the endpoint responds `200` with `results: []` — not an error, since "no matches because nothing exists yet" is a normal, expected state, not a failure. If FAISS returns a `vector_id` with no matching Postgres row (should not happen in practice — persistence and FAISS writes are meant to happen together in `embed_and_persist` — but ERP-011's "Known Limitations" section already documents that the two stores aren't written atomically), that hit is silently dropped from the response rather than raising, since a stale/orphaned vector shouldn't break a query for everything else.

## Request/Response Details

- `top_k`: optional, default `5`, validated via Pydantic `Field(default=5, ge=1, le=50)` — the upper bound prevents a single request from forcing an expensive full-index scan or an oversized response.
- `query`: required, `Field(min_length=1)` — an empty string is rejected with a `422` before any embedding call is made.
- No per-document filtering in this slice — search always runs across every ingested chunk.
- Each `RetrievedChunk` mirrors `app/ingestion/schemas.py`'s `Chunk` fields (`chunk_id`, `document_id`, `text`, `section_path`, `page_start`, `page_end`, `source_filename`) plus the new `score`, so a caller gets the same provenance data ingestion already produces.

## FAISS Index Access

`service.search` constructs a fresh `FaissIndex(settings.faiss_index_path, settings.dimension)` per request — the same "load fresh, no cross-request caching" approach `embed_and_persist` already uses. This is a deliberate non-optimization: it trades some per-request latency (re-reading the index file from disk) for always seeing the latest saved index and for not introducing a new caching-invalidation concern on top of the FAISS write-concurrency limitation ERP-011 already documented. Revisiting this (e.g. a long-lived, lock-guarded in-memory index) is left for whenever there's a concrete performance reason to.

## Testing

- `FaissIndex.search`: unit tests against a temp-directory index (same pattern as ERP-011's `test_index.py`) — add known vectors, search, assert nearest-first ordering and correct `vector_id`s.
- `get_chunks_by_vector_ids`: tests run against the real test Postgres (per ADR-003, consistent with the rest of the repository layer).
- `service.search`: the embedding call is mocked (a fake `EmbeddingClient` returning a fixed vector, same pattern as `test_service.py` in ERP-011); FAISS search and the Postgres lookup run for real. Covers: normal results, empty index, and a `top_k` larger than the number of ingested chunks (should return all of them, not error).
- `router.py`: an end-to-end test through `POST /retrieval/query`, mirroring `test_router.py`'s existing pattern of stubbing the embedding call so no live Ollama server is required.
- The 90%-coverage CI gate (ERP-006) applies to all new/modified modules.

## New Dependencies

None. `faiss-cpu`, `sqlalchemy`, and the `ollama` client are all already dependencies from ERP-011.
