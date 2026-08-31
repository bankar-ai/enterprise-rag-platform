# Session — Reranking (ERP-015)

Date: 2026-08-26
Tickets Touched: ERP-015

## Decisions

- Reranker choice: FlashRank (`ms-marco-TinyBERT-L-2-v2`, ONNX, no `torch`) — see `.ai/adr/ADR-006.md` for the full research. Chosen over LLM-as-reranker (lower measured NDCG, higher per-query latency, fragile output parsing despite reusing the existing Ollama dependency) and over `sentence-transformers`' `CrossEncoder` (pulls in `torch`, disproportionate for this project's scale). Confirmed via `uv add flashrank` that it resolves against already-satisfied dependencies with minimal lockfile impact — no `torch`.
- Kept strictly optional and additive: `RetrievalQuery.rerank: bool = False`. When omitted/false, `service.search` never constructs or invokes a reranker — zero added latency/cost for existing callers, and ERP-012/ERP-014's existing behavior and tests are untouched.
- `Reranker` is a `Protocol` + `FlashRankReranker` implementation, injectable into `service.search` the same way `embedding_client`/`faiss_index` already are — keeps the door open for a different reranking backend later (e.g. an LLM-based one) without touching the endpoint contract.
- Reranked `RetrievedChunk.score` reflects the cross-encoder's own score, replacing the RRF score for that request only — consistent with the existing precedent (ERP-014 already redefined `score`'s meaning once, from `1/(1+distance)` to an RRF score; `score` has always been documented as an opaque ranking signal, not a stable cross-request unit).

## Implementation Summary

- `app/retrieval/config.py` (new): `RerankerSettings` (`RERANKER_` env prefix) — `model_name`, `cache_dir`.
- `app/retrieval/reranker.py` (new): `Reranker` protocol; `FlashRankReranker` wraps `flashrank.Ranker`/`RerankRequest`, short-circuits to `[]` for empty candidates without touching the model.
- `app/retrieval/schemas.py`: `RetrievalQuery.rerank: bool = Field(default=False)`.
- `app/retrieval/service.py`: `search` gained `rerank: bool = False` and an injectable `reranker: Reranker | None = None`; when `rerank` is true, the fused+hydrated results are passed through the reranker (built lazily via `get_reranker_settings()` if not injected) and returned in its order.
- `app/retrieval/router.py`: passes `request.rerank` through to `search`.
- New dependency: `flashrank>=0.2.10` (justified in ADR-006).

Three commits on `erp-015-reranking` (stacked on `erp-014-bm25-hybrid-retrieval`):
- `f5c8178` docs: add ERP-015 ticket, design spec, plan, ADR-006 (reranker choice); add flashrank dependency
- `cbec057` feat: add FlashRank-backed reranker
- `262915b` feat: wire optional reranking into the retrieval endpoint

Full suite: 86 passed, 99.34% coverage (gate: 90%). `ruff check .` and `mypy app` both clean.

## Blockers

None. The default FlashRank model downloads a small (~3MB) ONNX weight file to `RerankerSettings.cache_dir` on first use — not a paid API call, but does require network access the first time a process reranks anything (mirrors how Ollama pulls model weights on first use). Tests exercise the real model once (`tests/retrieval/test_reranker.py`) rather than mocking it, since the model is small and fast enough (<4s including download-if-needed) not to justify the extra indirection of a fake.

## Next Steps

- ERP-016 — PageIndex-style structure-aware retrieval using `section_path`, the last of the three deferred ERP-012 follow-ups.
- Redis embedding cache (still deferred from ERP-011).
- Open a PR for `erp-015-reranking` into `develop` (stacked on the still-open ERP-014 PR; not merged — left for review).
