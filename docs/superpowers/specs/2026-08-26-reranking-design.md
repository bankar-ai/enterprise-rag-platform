# Reranking Design

Date: 2026-08-26

## Context

ERP-014 fused BM25 and vector retrieval into one RRF-ranked candidate list. RRF is rank-position-based and cheap, but it has no notion of actual query-passage semantic relevance beyond what each retriever's own ranking already captures. Reranking adds a precision pass: a cross-encoder that jointly scores `(query, passage_text)` pairs, generally more accurate than either retriever alone at the cost of extra local compute. `current-state.md` lists this as the second of three deferred ERP-012 follow-ups.

## Scope

In scope: an optional reranking step over `service.search`'s fused candidate list, gated by a new `RetrievalQuery.rerank` flag.

Out of scope: PageIndex-style retrieval (ERP-016), always-on reranking, reranker model configurability beyond env-var settings, batching/async reranking across concurrent requests.

## Reranker Choice

FlashRank, default model `ms-marco-TinyBERT-L-2-v2` (ONNX runtime, ~4M parameters, no `torch`) — see `.ai/adr/ADR-006.md` for the full research, benchmark comparison against LLM-as-reranker, and rejected alternatives.

## Module Changes

- `app/retrieval/config.py` (new): `RerankerSettings` (`RERANKER_` env prefix) — `model_name: str = "ms-marco-TinyBERT-L-2-v2"`, `cache_dir: str = "data/reranker_cache"`. Mirrors `app/embedding/config.py`'s shape.
- `app/retrieval/reranker.py` (new): `Reranker` `Protocol` (`rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]`) and `FlashRankReranker`, wrapping `flashrank.Ranker`/`RerankRequest`. Empty `candidates` short-circuits to `[]` without constructing a request.
- `app/retrieval/schemas.py`: `RetrievalQuery` gains `rerank: bool = Field(default=False)`.
- `app/retrieval/service.py`: `search` gains an optional `reranker: Reranker | None = None` injection parameter and a `rerank: bool = False` parameter. When `rerank=True`, after RRF fusion + hydration, the hydrated `RetrievedChunk` list is passed through the reranker (constructing a default `FlashRankReranker` if none was injected) and returned in the reranker's order with `score` replaced by the reranker's score. When `False` (default), this whole path is skipped — no reranker constructed, no extra latency.
- `app/retrieval/router.py`: passes `request.rerank` through to `search`.

## Data Flow

`POST /retrieval/query {"query": "...", "top_k": 5, "rerank": true}` → `search()` runs ERP-014's hybrid fusion as before → if `rerank` is true, the fused+hydrated candidates are handed to `Reranker.rerank(query, candidates)`, which scores each `(query, candidate.text)` pair with the cross-encoder and returns candidates sorted by that score, descending → the endpoint returns the reranked list. If `rerank` is false or omitted, the response is identical to ERP-014's.

## Testing

- `FlashRankReranker`: one real (not mocked) test using the actual small local ONNX model — asserts a passage on-topic for the query outranks an off-topic one; an empty-candidates test asserts `[]` without needing the model loaded (checked via a spy/counter, no network call).
- `service.search`: `reranker` is injectable, same pattern as `embedding_client`/`faiss_index` — tests inject a fake `Reranker` to assert it's *not* called when `rerank=False` and *is* called with the fused candidates when `rerank=True`, and that its returned order/scores flow through untouched.
- `router.py`: extend the existing end-to-end test with a `rerank: true` request, asserting `200` and non-empty results; existing `rerank`-omitted tests must keep passing byte-for-byte.
- 90% coverage gate applies to all new/modified modules.

## New Dependencies

`flashrank` — see `.ai/adr/ADR-006.md` for justification (no `torch`; lockfile diff confirmed minimal transitive footprint).
