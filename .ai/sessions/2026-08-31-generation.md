# Session — Generation (ERP-017)

Date: 2026-08-31
Tickets Touched: ERP-017

## Decisions

- Sync-only response: no streaming (SSE/chunked). Deferred to `docs/roadmap.md` as later work, not part of this slice.
- New standalone `POST /generation/query` endpoint, rather than merging generation into `POST /retrieval/query` — keeps retrieval's existing contract (ERP-012/014/015/016) completely untouched and lets a caller opt into synthesis explicitly.
- Inline numbered citations: the LLM is instructed to cite as `[1]`, `[2]`, ... inline in the answer text, and the response carries a parallel `citations: list[Citation]` (chunk_id, document_id, section_path, page_start, page_end, source_filename) in the same order, so the numbering is traceable back to real chunks without server-side parsing of the answer text.
- Retrieval knobs (`top_k`, `rerank`, `expand_sections`) are exposed on `GenerationQuery` and passed straight through to `app.retrieval.service.search` unmodified — generation adds no new ranking behavior, it only consumes retrieval's existing output.
- Context truncation is char-budget-based (`max_context_chars`, default 8000), not a fixed chunk count: `build_prompt` walks ranked chunks accumulating character count and stops before the chunk that would exceed the budget, so truncation always drops the lowest-ranked tail rather than an arbitrary cut or a partially-included chunk.
- Empty-retrieval short-circuit: if `retrieval.service.search` returns no chunks, `service.generate` returns a fixed "not enough information" `GenerationResponse` with `citations=[]` without constructing an `LLMClient` or making any Ollama call — avoids paying LLM latency/cost on a request that can't be grounded.
- No new dependency: `ollama` was already present (used by `app/embedding/client.py` for embeddings) and covers chat completion too.

## Implementation Summary

New `app/generation/` module, mirroring `app/retrieval/`'s and `app/embedding/`'s shape:

- `app/generation/config.py` — `GenerationSettings` (`GENERATION_` env prefix): `ollama_host`, `model = "qwen3"`, `max_context_chars = 8000`, `temperature = 0.1`; `get_generation_settings()` (`lru_cache`-wrapped).
- `app/generation/client.py` — `LLMClient` `Protocol` (`generate(system_prompt, user_prompt) -> str`) and `OllamaLLMClient`, wrapping `ollama.Client.chat`, injectable for tests same as `EmbeddingClient`/`OllamaEmbeddingClient`.
- `app/generation/prompt.py` — fixed `SYSTEM_PROMPT` (answer only from numbered context, cite inline as `[1]`, `[2]`, ..., say explicitly when context is insufficient); `build_prompt(query, chunks, max_context_chars) -> tuple[str, list[RetrievedChunk]]` with the char-budget truncation described above.
- `app/generation/schemas.py` — `GenerationQuery` (mirrors `RetrievalQuery`: `query`, `top_k`, `rerank`, `expand_sections`), `Citation` (`RetrievedChunk` minus `text`/`score`), `GenerationResponse` (`answer: str`, `citations: list[Citation]`).
- `app/generation/service.py` — `generate(...)` orchestration: calls `retrieval.service.search` unmodified, empty-result short-circuit, otherwise `build_prompt` + `llm_client.generate` + `Citation` construction from the included chunks.
- `app/generation/router.py` — `POST /generation/query`, same try/except → `HTTPException(503)` pattern as `app/retrieval/router.py`.
- `app/main.py` — registers the new `generation` router alongside `retrieval`.

Six commits on this branch (`erp-017-generation`):
- `d7860b5` feat: add GenerationSettings config for ERP-017
- `6b6a7f0` feat: add OllamaLLMClient for ERP-017
- `d38d0ef` feat: add prompt construction with char-budget truncation for ERP-017
- `0760f8d` feat: add generation request/response schemas for ERP-017
- `dfa432e` feat: add generate() service orchestration for ERP-017
- `64aed2e` feat: add POST /generation/query endpoint for ERP-017

Full suite: 126 passed, 99.52% coverage (gate: 90%).

## Blockers

None. All six tasks were reviewed and approved (clean or with only deferred, non-blocking minors — see below).

Minor findings deferred during code review, none blocking:
- A test-only `conftest.py` override to skip the DB fixture for generation tests (Task 1).
- No error handling around malformed Ollama responses in `OllamaLLMClient.generate` (Task 2) — acceptable, the router's 503 catch-all covers it at the boundary.
- The context character budget is computed from raw chunk text only, not the fully rendered prompt block, so it slightly under-represents real prompt size (Task 3).
- A few schema/injection-fallback code paths are exercised implicitly rather than by a dedicated test (Tasks 4-5).
- A router docstring wording nitpick (Task 6).

## Next Steps

- Open a PR for `erp-017-generation` into `develop`, summarizing the six `app/generation/*` modules and linking the design spec and this session log. Not merged — left for review.
- Streaming responses and multi-turn conversation memory remain on `docs/roadmap.md` as later work, out of scope for this ticket.
