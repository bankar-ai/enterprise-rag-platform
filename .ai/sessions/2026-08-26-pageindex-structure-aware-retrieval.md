# Session — PageIndex-Style Structure-Aware Retrieval (ERP-016)

Date: 2026-08-26
Tickets Touched: ERP-016

## Decisions

- Implemented a lighter-weight "section-sibling expansion" instead of a real hierarchical-tree/reasoning-based PageIndex traversal — see `.ai/adr/ADR-007.md` for the full reasoning. The data model stores `section_path` as a flat, denormalized list per chunk (no persisted tree/parent-child structure), so building an actual tree index and traversal strategy (possibly LLM-driven, same latency/cost trade-off already rejected for reranking in ADR-006) would be a much larger, unscoped change than this ticket's slot in the ERP-012 follow-up sequence.
- `get_sibling_chunks` filters by section equality in Python, not SQL, because `chunks.section_path` is a Postgres `json` column (not `jsonb`), and `json` has no `=` operator in Postgres. Comparing in Python against the already-indexed `document_id`'s chunk set avoids a brittle text-cast comparison and avoids a schema/column-type change.
- `expand_sections=True` appends section-siblings after fusion and after any reranking, inheriting each anchor's score, and the response can legitimately exceed `top_k` — documented as intended, not re-truncated, since the point is fuller context, not a fixed result count.
- Composes cleanly with `rerank`: expansion runs last, using whatever order (fused or reranked) is already established.

## Implementation Summary

- `app/ingestion/repository.py`: `get_sibling_chunks(session, document_id, section_path, exclude_chunk_ids=frozenset()) -> list[ChunkRecord]`.
- `app/retrieval/schemas.py`: `RetrievalQuery.expand_sections: bool = Field(default=False)`.
- `app/retrieval/service.py`: `_expand_sections(session, results)` helper; `search` gained `expand_sections: bool = False`, applied inside the existing DB session block, after fusion and after any reranking.
- `app/retrieval/router.py`: passes `request.expand_sections` through.
- `.ai/adr/ADR-007.md`: traversal-strategy ADR (section-sibling expansion vs. a real tree index, weighted section-score aggregation, and a `jsonb` schema change).

This closes out the three-ticket ERP-012 follow-up sequence started with ERP-014 (BM25 hybrid retrieval) and continued with ERP-015 (reranking).

Three commits on `erp-016-pageindex-retrieval` (stacked on `erp-015-reranking`):
- `8536e5f` docs: add ERP-016 ticket, design spec, plan, and ADR-007 (section-expansion strategy)
- `1367f7d` feat: add section-sibling lookup for structure-aware retrieval
- `de492d8` feat: add optional section-sibling expansion to the retrieval endpoint

Full suite: 95 passed, 99.37% coverage (gate: 90%). `ruff check .` and `mypy app` both clean. No new dependencies.

## Blockers

None.

## Next Steps

- Redis embedding cache (still deferred from ERP-011) is the only remaining item in `current-state.md`'s "Next Planned Work"/"What Does Not Exist Yet".
- Consider requiring CI status checks in `main`'s branch protection (long-standing item, unrelated to this ticket).
- Once ERP-014/ERP-015/ERP-016's stacked PRs are reviewed and merged in order, a follow-up could consolidate the three sessions/tickets' learnings (Postgres `json` vs `jsonb` for structured metadata, RRF vs. score-normalization fusion, purpose-built vs. LLM rerankers) into a short retrospective note if useful.
