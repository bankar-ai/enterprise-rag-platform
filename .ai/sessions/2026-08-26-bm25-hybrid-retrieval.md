# Session — BM25 Hybrid Retrieval (ERP-014)

Date: 2026-08-26
Tickets Touched: ERP-014

## Decisions

- BM25 uses Postgres's native full-text search rather than a new dependency: `chunks` gains a generated (`GENERATED ALWAYS AS ... STORED`) `search_vector` `TSVECTOR` column plus a GIN index, computed from `text` by the database itself so it can never drift out of sync with the source column. Modeled via SQLAlchemy's `Computed(...)`, added via a new Alembic migration mirroring the existing migration's raw-`op.*` style.
- Fusion algorithm: Reciprocal Rank Fusion (RRF, `k=60`), chosen after web research per `CLAUDE.md`'s "Research Before Recommending" rule — see `.ai/adr/ADR-005.md` for the full write-up and sources (Azure AI Search, Weaviate, and Elasticsearch hybrid-search docs all default to RRF for exactly this BM25+vector combination). Rejected alternatives: weighted linear combination of normalized raw scores (needs a tunable `alpha` with no principled default) and BM25-as-tiebreaker-only (doesn't really combine both signals).
- `RetrievedChunk.score` is now an RRF score, not `1/(1+distance)` — a behavior change to score *values* but not to the response *contract* (still a float, still higher-is-better, still opaque/ranking-only). No known consumer depends on the old formula.
- Endpoint contract kept additive: `RetrievalQuery`, `RetrievalResponse`, `RetrievedChunk`, and `POST /retrieval/query`'s signature are all unchanged; only the ranking algorithm inside `service.search` changed.
- BM25 search runs across the whole `chunks` table (no per-document filtering), same scope boundary as vector search — consistent with ERP-012, not a new limitation introduced here.

## Implementation Summary

- `app/ingestion/models.py`: `ChunkRecord.search_vector` (`TSVECTOR`, `Computed`).
- `alembic/versions/a97a8780506f_...py`: adds `search_vector` + `ix_chunks_search_vector` (GIN); downgrade drops both. Applied and verified against local Postgres.
- `app/ingestion/repository.py`: `search_chunks_by_text(session, query_text, k) -> list[tuple[int, float]]` — `plainto_tsquery` + `ts_rank`, best-first, `[]` for blank query/`k<=0`/no matches.
- `app/retrieval/service.py`: new `_reciprocal_rank_fusion(*ranked_id_lists, k=60)` helper; `search` now runs BM25 and vector retrieval, fuses their rank-ordered `vector_id` lists via RRF, hydrates the fused top-`top_k` from Postgres, and returns `RetrievedChunk`s in fused-score order. Existing empty-index and orphaned-`vector_id` handling preserved.
- `.ai/adr/ADR-005.md`: fusion-algorithm ADR with research sources.
- Tests: `tests/ingestion/test_repository.py` (BM25 ranking, no-match, empty-query), `tests/retrieval/test_service.py` (RRF helper unit tests; vector-empty/BM25-hits, both-hit-with-overlap, existing ERP-012 scenarios re-verified against the new fused scoring), `tests/ingestion/test_models.py` (updated column-set assertion).

Five commits on `erp-014-bm25-hybrid-retrieval`:
- `ce9d085` feat: add generated tsvector column and GIN index to chunks
- `916c73e` feat: add BM25 full-text search repository method
- `f9ccd82` feat: fuse BM25 and vector retrieval via RRF in search service
- `38cd1fe` docs: add ERP-014 ticket, design spec, plan, and ADR-005 (RRF fusion choice)
- (this commit) docs: close out ERP-014 ticket, session log, and current-state

Full suite: 81 passed, 99.30% coverage (gate: 90%). `ruff check .` and `mypy app` both clean. No new dependencies.

## Blockers

None. One environmental note worth recording: the shared local test Postgres (`erp_test`, used by `tests/conftest.py`'s session-scoped schema fixture) is a single instance reused across worktrees/sessions with no per-test transaction isolation — two BM25 tests initially used generic text ("giraffes... Africa") that collided with identically-worded fixtures elsewhere in the suite, producing flaky extra matches. Fixed by giving cross-document full-text tests distinctive, non-recurring vocabulary rather than by adding test isolation infrastructure (out of scope here).

## Next Steps

- ERP-015 — Reranking: an optional post-processing step over this ticket's fused candidate list. Requires web-researching current open-source/free reranker options before choosing one (per `CLAUDE.md`'s research rule).
- ERP-016 — PageIndex-style structure-aware retrieval using `section_path`.
- Redis embedding cache (still deferred from ERP-011).
- Open a PR for `erp-014-bm25-hybrid-retrieval` into `develop` (not merged — left for review).
