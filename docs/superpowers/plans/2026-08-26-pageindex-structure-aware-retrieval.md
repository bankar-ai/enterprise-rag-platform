# PageIndex-Style Structure-Aware Retrieval Implementation Plan

**Goal:** Add an opt-in `expand_sections` mode that surfaces each ranked chunk's full section (via `section_path`-matched siblings), composing on top of ERP-014's fusion and ERP-015's optional reranking.

**Architecture:** New `get_sibling_chunks` repository method (Python-side equality filter, see ADR-007), a new `_expand_sections` service helper, and an additive `RetrievalQuery.expand_sections` flag.

**Tech Stack:** No new dependencies — plain Python + existing Postgres access.

## Global Constraints

Same as ERP-014/015: `uv`-only deps (none needed), no `print()`, mypy `--strict`, 90% coverage gate, routes thin, `expand_sections=False` must leave existing behavior/tests untouched, response may exceed `top_k` when `expand_sections=True` (intended).

---

### Task 1: `get_sibling_chunks`

- Add to `app/ingestion/repository.py`: `get_sibling_chunks(session, document_id, section_path, exclude_chunk_ids=frozenset()) -> list[ChunkRecord]`.
- Tests in `tests/ingestion/test_repository.py`: three chunks (two same section, one different) — assert correct siblings, `exclude_chunk_ids` honored, `chunk_index` ordering.
- Verify: `uv run pytest tests/ingestion/test_repository.py -v`
- Commit: `feat: add section-sibling lookup for structure-aware retrieval`

### Task 2: `_expand_sections` + wiring

- Add `_expand_sections(session, results)` to `app/retrieval/service.py`; `search` gains `expand_sections: bool = False`, calling it inside the existing session block after fusion/reranking.
- `app/retrieval/schemas.py`: `RetrievalQuery.expand_sections: bool = Field(default=False)`.
- `app/retrieval/router.py`: pass `request.expand_sections` through.
- Tests: `tests/retrieval/test_service.py` (`_expand_sections` unit behavior + `search` with `expand_sections=True`/`False`, combined with `rerank=True`), `tests/retrieval/test_schemas.py` (default `False`), `tests/retrieval/test_router.py` (end-to-end `expand_sections: true`).
- Verify: `uv run pytest tests/retrieval/ tests/ingestion/ -v`
- Commit: `feat: add optional section-sibling expansion to the retrieval endpoint`

### Task 3: Full verification + close-out

- `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90`
- `uv run ruff check .` / `uv run mypy app`
- Update `.ai/tickets/ERP-016.md` to Done; write `.ai/sessions/2026-08-26-pageindex-structure-aware-retrieval.md`; update `.ai/memory/current-state.md` (this closes out the three-ticket ERP-012 follow-up sequence).
- Commit: `docs: close out ERP-016 ticket, session log, and current-state`
- Push branch (stacked on `erp-015-reranking`), open PR into `develop`.
