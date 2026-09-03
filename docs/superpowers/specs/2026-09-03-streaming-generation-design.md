# Streaming Generation Design

Date: 2026-09-03

## Context

`POST /generation/query` (ERP-017/018/019) is sync-only: the caller waits for the full LLM completion before getting any response. `docs/roadmap.md` names Streaming Responses as a distinct roadmap item, and `current-state.md` flags it as "the largest remaining capability gap" after ERP-019. This ticket (ERP-020) adds a streaming counterpart so a client can render the answer token-by-token instead of waiting on the full generation, without changing the existing endpoint's contract.

## Scope

In scope: a new `POST /generation/query/stream` endpoint, same `GenerationQuery` request body as the existing endpoint (including `conversation_id` — full conversation-memory parity with `POST /generation/query`), returning a Server-Sent Events (SSE) response instead of one JSON body.

Out of scope: changes to `POST /generation/query` or `GET /conversations/{id}` (both untouched, byte-for-byte); changes to retrieval, reranking, or section expansion; partial-answer persistence on disconnect/error (explicitly rejected — see Error Handling); any new client-side/UI work.

## Module Changes

- `app/generation/client.py`: `LLMClient` `Protocol` gains `generate_stream(system_prompt: str, user_prompt: str) -> Iterator[str]`, yielding successive text chunks. `OllamaLLMClient.generate_stream` calls `ollama.Client.chat(..., stream=True)` and yields `chunk.message.content` for each streamed chunk (skipping empty/`None` content). `generate` (existing, non-streaming) is unchanged.
- `app/generation/service.py`: new `generate_stream(query, top_k, rerank, expand_sections, conversation_id, settings, llm_client) -> Iterator[StreamEvent]`, a generator mirroring `generate()`'s existing stateless/stateful branching (history load, rewrite, retrieval, empty-context short-circuit) but yielding structured events instead of returning one `GenerationResponse`. `generate()` itself is untouched — no shared control flow is extracted solely for this ticket; the duplication between the two branches is small and keeping them independent avoids coupling the sync endpoint's behavior to the streaming generator's.
- `app/generation/schemas.py`: new `StreamEvent` union (or three small models — `CitationsEvent`, `TokenEvent`, `DoneEvent`, `ErrorEvent`) used internally by the service/router to type the generator's yields; not part of the public OpenAPI schema (SSE responses aren't represented by a Pydantic response model in FastAPI).
- `app/generation/router.py`: new `POST /generation/query/stream`, building a `StreamingResponse(media_type="text/event-stream")` from `generate_stream(...)`, serializing each `StreamEvent` to the wire format below. Existing `POST /generation/query` handler unchanged.

## Wire Format

Standard SSE framing (`event: <name>\ndata: <json>\n\n`), one event stream per request:

1. `event: citations`, `data: {"citations": [...]}` — emitted once, immediately after retrieval resolves (citations are known before generation starts; empty list on the no-context short-circuit).
2. `event: token`, `data: {"text": "..."}` — emitted once per chunk yielded by `LLMClient.generate_stream` (or once, for the fixed `NO_CONTEXT_ANSWER` string, on the short-circuit path — kept as a single event since there's no LLM call to chunk).
3. `event: done`, `data: {"conversation_id": "<uuid>" | null}` — emitted once, after the full answer text is assembled and (for a stateful request) persistence has committed. Terminal event on success.
4. `event: error`, `data: {"detail": "..."}` — emitted in place of `done` if anything fails after streaming has started (see Error Handling). Terminal event on failure.

## Data Flow

`POST /generation/query/stream {"query": "...", "conversation_id": "..."}` → router opens `StreamingResponse` immediately (200 OK, headers sent) → `generate_stream` runs the same branch selection as `generate()`: stateless (`conversation_id is None`) skips straight to retrieval; stateful loads history, rewrites the query if history is non-empty → retrieval runs (`rerank`/`expand_sections` passed through unmodified) → `citations` event yielded → if chunks is empty, a single `token` event carries `NO_CONTEXT_ANSWER` and the answer variable is set to it directly (no LLM call); otherwise `build_prompt` runs as today and `llm_client.generate_stream(SYSTEM_PROMPT, user_prompt)` is iterated, each chunk both yielded as a `token` event and appended to an accumulator → once iteration completes, the full answer string is known → for a stateful request, both turns are persisted in one transaction (same `get_or_create_conversation`/`append_message`/commit sequence as `generate()`, run only at this point) → `done` event yielded, generator returns.

## Error Handling

Because the SSE response's headers (200 OK) are sent as soon as streaming starts, an exception can no longer become an HTTP error status — `POST /generation/query`'s `try/except → HTTPException(503)` pattern doesn't apply here. Instead, the entire generator body (from retrieval through persistence) runs inside a `try/except Exception`; on failure, `logger.exception` logs it and a single `error` event is yielded with a generic `detail` (no internal exception text leaked to the client), then the generator returns without persisting.

Partial persistence is explicitly out of scope: persistence only happens after the full answer is assembled, as the last step before the `done` event. A client disconnect mid-stream causes Starlette to close the generator early (`GeneratorExit` raised at the suspended `yield`), which skips the remaining generator body — including persistence — with no special-case code needed. A mid-generation exception hits the `except` branch instead and also never reaches the persistence step. Both cases leave the conversation's history exactly as it was before the request, matching `POST /generation/query`'s all-or-nothing transaction semantics.

## Testing

- `OllamaLLMClient.generate_stream`: monkeypatch `ollama.Client.chat` to return a fixed iterable of chunk objects; assert `generate_stream` yields their `.message.content` in order, and that empty/`None` content chunks are skipped.
- `service.generate_stream`: inject a fake `LLMClient` whose `generate_stream` yields a fixed sequence of strings; assert the event sequence (`citations` → `token`×n → `done`) and payloads for: stateless request, stateful request with empty history, stateful request with prior history (rewrite invoked), empty-retrieval short-circuit (single `token` event, no `LLMClient.generate_stream` call), and an exception raised partway through iteration (asserts `error` event yielded, no persistence call made — mock/spy the repository functions to confirm).
- `router.py`: `TestClient` can consume a `StreamingResponse` synchronously; assert the raw SSE body's event/data lines for a stubbed success path and a stubbed mid-stream failure path.
- 90% coverage gate applies to all new/changed code, per existing CI configuration.

## New Dependencies

None — `ollama`'s `Client.chat(..., stream=True)` and FastAPI's built-in `StreamingResponse` cover this; no new package needed.
