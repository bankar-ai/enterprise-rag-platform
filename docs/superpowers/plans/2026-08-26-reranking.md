# Reranking Implementation Plan

**Goal:** Add an optional cross-encoder reranking pass (FlashRank) over `service.search`'s fused candidate list, gated by a new `rerank: bool = False` request field — zero behavior/latency change when not opted into.

**Architecture:** New `app/retrieval/config.py` (`RerankerSettings`) and `app/retrieval/reranker.py` (`Reranker` protocol + `FlashRankReranker`), wired into `service.search` as an injectable parameter, same pattern as `embedding_client`/`faiss_index`.

**Tech Stack:** `flashrank` (new dependency, justified in ADR-006) — ONNX runtime under the hood, no `torch`.

## Global Constraints

`uv add flashrank` already done; `uv`-only for any further dependency changes. No `print()`. Mypy `--strict` on `app/`. 90% coverage gate. Routes stay thin. Default `rerank=False` must not change ERP-014's existing behavior or tests.

---

### Task 1: `RerankerSettings` + `Reranker` protocol + `FlashRankReranker`

- Create `app/retrieval/config.py`: `RerankerSettings` (`RERANKER_` prefix), `get_reranker_settings()` (`lru_cache`), mirroring `app/embedding/config.py`.
- Create `app/retrieval/reranker.py`: `Reranker` `Protocol`; `FlashRankReranker.__init__(settings)` builds a `flashrank.Ranker(model_name=..., cache_dir=...)`; `.rerank(query, candidates)` builds a `RerankRequest` from `[{"id": i, "text": c.text} for i, c in enumerate(candidates)]`, calls `ranker.rerank(request)`, maps results back to `RetrievedChunk`s (by index `id`) with `score` replaced, returns best-first. `[]` candidates short-circuits before touching the model.
- Tests in `tests/retrieval/test_reranker.py`: real `FlashRankReranker` test (on-topic beats off-topic candidate); empty-candidates test using a `Ranker` stand-in/spy to confirm no call is made.
- Verify: `uv run pytest tests/retrieval/test_reranker.py -v`
- Commit: `feat: add FlashRank-backed reranker`

### Task 2: Wire `rerank` into schemas, service, router

- `app/retrieval/schemas.py`: `RetrievalQuery.rerank: bool = Field(default=False)`.
- `app/retrieval/service.py`: `search(..., rerank: bool = False, reranker: Reranker | None = None)`; after fusion+hydration, if `rerank`, lazily construct `reranker or FlashRankReranker(get_reranker_settings())` and replace `results` with `reranker.rerank(query, results)`.
- `app/retrieval/router.py`: `search(request.query, request.top_k, rerank=request.rerank)`.
- Tests: extend `tests/retrieval/test_schemas.py` (`rerank` defaults `False`), `tests/retrieval/test_service.py` (fake `Reranker` — not called when `rerank=False`; called and its output used when `rerank=True`), `tests/retrieval/test_router.py` (end-to-end `rerank: true` request).
- Verify: `uv run pytest tests/retrieval/ -v`
- Commit: `feat: wire optional reranking into the retrieval endpoint`

### Task 3: Full verification + close-out

- `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=90`
- `uv run ruff check .` / `uv run mypy app`
- Confirm all ERP-012/ERP-014 tests still pass unmodified in behavior.
- Update `.ai/tickets/ERP-015.md` to Done; write `.ai/sessions/2026-08-26-reranking.md`; update `.ai/memory/current-state.md`.
- Commit: `docs: close out ERP-015 ticket, session log, and current-state`
- Push branch (stacked on `erp-014-bm25-hybrid-retrieval`), open PR into `develop`.
