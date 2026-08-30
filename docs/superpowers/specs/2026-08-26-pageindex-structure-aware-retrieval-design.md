# PageIndex-Style Structure-Aware Retrieval Design

Date: 2026-08-26

## Context

ERP-014 and ERP-015 fused and reranked flat chunk-level candidates. Neither uses the document *structure* chunks already carry: `section_path: list[str]`, the chunk's full heading hierarchy, set by ingestion's Markdown chunker (ERP-011). `docs/architecture.md` names "PageIndex-inspired retrieval" as a goal. This is the third and last deferred ERP-012 follow-up.

## Scope

In scope: an opt-in `expand_sections` mode that, given `service.search`'s already-ranked result list (post-fusion, post-optional-reranking), surfaces each ranked chunk's full section by including its section-siblings.

Out of scope (see `.ai/adr/ADR-007.md`): a real persisted hierarchical tree index, LLM/reasoning-based tree traversal, parent/ancestor-level expansion (only exact-section siblings, not parent or ancestor sections), re-truncating the response back to `top_k` after expansion.

## Module Changes

- `app/ingestion/repository.py`: new `get_sibling_chunks(session, document_id, section_path, exclude_chunk_ids=frozenset()) -> list[ChunkRecord]` — fetches all chunks for `document_id` (via the existing indexed `document_id` column), filters in Python to those whose `section_path` equals the given one and whose `chunk_id` isn't in `exclude_chunk_ids`, ordered by `chunk_index`. Filtering in Python (not a SQL predicate) sidesteps `chunks.section_path` being a Postgres `json` column, which has no `=` operator (see ADR-007).
- `app/retrieval/schemas.py`: `RetrievalQuery.expand_sections: bool = Field(default=False)`.
- `app/retrieval/service.py`: new `_expand_sections(session, results) -> list[RetrievedChunk]` — for each anchor in `results` (in order, skipping any already emitted), appends the anchor, then its not-yet-seen siblings (each converted to a `RetrievedChunk` with `score` copied from the anchor). Called from `search` (inside the existing DB session context, after fusion and after any reranking) when `expand_sections=True`.
- `app/retrieval/router.py`: passes `request.expand_sections` through to `search`.

## Data Flow

`POST /retrieval/query {"query": "...", "expand_sections": true}` → `search()` runs hybrid fusion (ERP-014) and, if requested, reranking (ERP-015), producing a ranked `results` list → if `expand_sections`, `_expand_sections` walks `results` in order; for each not-yet-emitted chunk, it is emitted, then `get_sibling_chunks` is queried for the rest of its section and any newly-seen siblings are emitted right after it (same score as the anchor) → the endpoint returns the expanded list, which may be longer than `top_k`.

## Testing

- `get_sibling_chunks`: real-Postgres test — three chunks in the same document, two sharing a `section_path` and one in a different section; asserts only the true siblings are returned, `exclude_chunk_ids` is honored, and ordering is by `chunk_index`.
- `_expand_sections`: unit test with a fake/real session — a two-chunk section where only one chunk was originally ranked; asserts the sibling appears right after its anchor with the anchor's score, and that a chunk with no siblings passes through unchanged. A duplicate-safe test confirms a sibling that was *already* separately ranked isn't duplicated.
- `service.search`: extend existing test file — `expand_sections=False` (default) leaves results identical to the ERP-014/015 baseline; `expand_sections=True` alone, and combined with `rerank=True`, both grow the result set correctly.
- `router.py`: one end-to-end test with `expand_sections: true` against an ingested multi-chunk document.
- 90% coverage gate applies to all new/modified modules.

## New Dependencies

None.
