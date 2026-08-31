# Generation Design

Date: 2026-08-31

## Context

Retrieval (ERP-012 through ERP-016) returns ranked, provenance-tagged chunks but never synthesizes an answer — every response is a list of passages the caller has to read themselves. `docs/architecture.md` names "Local LLM inference using Ollama" and "Modular RAG pipeline" as core project goals, and `current-state.md` flags generation as the largest capability gap in the platform: this is a *Retrieval-Augmented Generation* platform with no generation yet. This ticket (ERP-017) closes that gap with the minimum slice that produces a real, grounded, citable answer, without pulling in streaming or conversation memory — both already called out separately in `docs/roadmap.md` as later work.

## Scope

In scope: a new `POST /generation/query` endpoint that runs the existing hybrid retrieval pipeline unmodified, then synthesizes a single grounded answer with inline citations via a local Ollama-hosted LLM (Qwen3, per `docs/architecture.md`'s stack).

Out of scope: streaming responses (SSE/chunked), multi-turn conversation memory, structured per-claim citations, any change to `app/retrieval/*` beyond calling it as a library, reranker/embedding model changes.

## Module Changes

New `app/generation/` module, mirroring `app/retrieval/`'s and `app/embedding/`'s existing shape:

- `app/generation/config.py` (new): `GenerationSettings` (`GENERATION_` env prefix, mirrors `EmbeddingSettings`'s pattern) — `ollama_host: str` (same default as `EmbeddingSettings.ollama_host`), `model: str = "qwen3"`, `max_context_chars: int = 8000`, `temperature: float = 0.1`.
- `app/generation/client.py` (new): `LLMClient` `Protocol` (`generate(system_prompt: str, user_prompt: str) -> str`) and `OllamaLLMClient`, wrapping `ollama.Client.chat`. Injectable for testing, same pattern as `EmbeddingClient`/`OllamaEmbeddingClient`.
- `app/generation/prompt.py` (new): a fixed `SYSTEM_PROMPT` instructing the model to answer only from the numbered context, cite inline as `[1]`, `[2]`, etc., and explicitly say when the context is insufficient rather than use outside knowledge. `build_prompt(query: str, chunks: list[RetrievedChunk], max_context_chars: int) -> tuple[str, list[RetrievedChunk]]` walks `chunks` in ranked order, accumulating character count, and stops before the chunk that would exceed `max_context_chars` — so truncation always drops the lowest-ranked tail, never an arbitrary cut. Returns the user-prompt text plus the (possibly truncated) list of chunks that were actually included, in citation-number order.
- `app/generation/schemas.py` (new): `GenerationQuery` (mirrors `RetrievalQuery`: `query: str = Field(min_length=1)`, `top_k: int = Field(default=5, ge=1, le=50)`, `rerank: bool = Field(default=False)`, `expand_sections: bool = Field(default=False)`), `Citation` (`chunk_id`, `document_id`, `section_path`, `page_start`, `page_end`, `source_filename` — `RetrievedChunk` minus `text`/`score`), `GenerationResponse` (`answer: str`, `citations: list[Citation]`).
- `app/generation/service.py` (new): `generate(query, top_k, rerank, expand_sections, settings=None, llm_client=None, ...) -> GenerationResponse`. Calls `app.retrieval.service.search(query, top_k, rerank=rerank, expand_sections=expand_sections)` unmodified. If the result is empty, short-circuits to a fixed "not enough information" `GenerationResponse` with `citations=[]`, without constructing an `LLMClient`. Otherwise calls `build_prompt`, then `llm_client.generate(SYSTEM_PROMPT, user_prompt)`, and returns `GenerationResponse(answer=..., citations=[Citation(...) for chunk in included_chunks])`.
- `app/generation/router.py` (new): `POST /generation/query`, same shape and try/except → `HTTPException(503)` pattern as `app/retrieval/router.py`.
- `app/main.py` (or wherever routers are registered): include the new `generation` router alongside `retrieval`.

## Data Flow

`POST /generation/query {"query": "...", "top_k": 5, "rerank": true, "expand_sections": false}` → `service.generate` calls `retrieval.service.search` with the same params (ERP-014/015/016 hybrid fusion, optional rerank, optional section expansion — all unchanged) → if `search` returns `[]`, return the fixed no-information response immediately → else `build_prompt` numbers the chunks `[1]..[n]`, truncating to `max_context_chars`, and renders `Question: {query}` beneath them → `OllamaLLMClient.generate` sends `(SYSTEM_PROMPT, user_prompt)` to Ollama's chat API for `model` at `temperature` → the raw answer text is returned as-is (the model is trusted to have followed the citation-marker instruction; no server-side validation of citation markers) alongside `citations`, the ordered list of chunks that back the `[1]..[n]` numbering.

## Error Handling

Any exception from retrieval or the LLM client (Ollama unreachable, model error, etc.) is caught in the router exactly like `retrieval/router.py` today: logged via `logger.exception`, re-raised as `HTTPException(503, "Generation query failed")`. No retries, no partial-answer fallback — matches the existing retrieval endpoint's failure contract.

## Testing

- `OllamaLLMClient`: monkeypatch `ollama.Client` the same way `tests/embedding/test_client.py` does for `OllamaEmbeddingClient` — assert it's called with the right model/messages/temperature and returns the response content; no real Ollama server involved.
- `build_prompt`: unit tests for the numbering format, the char-budget truncation boundary (chunk that would exceed the budget is excluded, not partially included), and the empty-chunks case.
- `service.generate`: `llm_client` is injectable, same pattern as `embedding_client`/`faiss_index`/`reranker` in `retrieval/service.py`. Tests inject a fake `search` (or monkeypatch `app.retrieval.service.search`) and a fake `LLMClient` to assert: (a) empty retrieval results short-circuit without constructing/calling `llm_client`; (b) non-empty results build a prompt from the right chunks and return `citations` matching the chunks actually included after truncation.
- `router.py`: end-to-end test asserting `200` with a stubbed retrieval+LLM path (following `tests/retrieval/test_router.py`'s stubbing style), and a `503` test on a simulated LLM-client failure.
- 90% coverage gate applies to all new modules, per existing CI configuration.

## New Dependencies

None — `ollama` (already a dependency via `app/embedding/client.py`) covers chat completion as well as embeddings; no new package needed.
