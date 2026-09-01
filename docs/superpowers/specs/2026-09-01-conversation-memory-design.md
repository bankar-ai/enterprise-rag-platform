# Conversation Memory Design

Date: 2026-09-01

## Context

`POST /generation/query` (ERP-017) is stateless and single-turn: every call retrieves and answers independently, with no notion of a prior exchange. `docs/roadmap.md` names "Conversation Memory" as a future feature, and ADR-003 already scoped Postgres to hold "conversation history" as one of its intended uses. This ticket (ERP-018) makes `/generation/query` multi-turn: a follow-up question like "what about the second one?" should resolve against what was actually discussed, not fail retrieval because the raw text carries no searchable signal.

Authentication is a separate, not-yet-built roadmap item. This design deliberately does not gate conversations behind any identity — a `conversation_id` is a bearer capability (like a session cookie without login), and the schema is designed so an ownership column can be added later without a breaking change.

## Scope

In scope: an optional `conversation_id` on `POST /generation/query`; Postgres-backed durable turn storage; LLM-based query rewriting for retrieval when history exists; a bounded (last-N-turn) history window fed into both rewriting and the generation prompt.

Out of scope: authentication/ownership of conversations (future ticket, once Authentication lands); a `GET /conversations/{id}` read endpoint (client already has every answer it received; a follow-up ticket once a real client needs to reload history); streaming responses (separate roadmap item); conversation deletion/retention policy beyond what Postgres already durably stores.

## Data Model

Two new tables, defined in `app/generation/models.py` using the same shared `Base` as `app/ingestion/models.py` (imported from there, not redefined — `alembic/env.py`'s `target_metadata` already targets this one `Base.metadata`; `alembic/env.py` gains an import of `app.generation.models` so its tables register for future autogenerate diffs too):

- `conversations`: `id: UUID` (primary key, server-generated via `gen_random_uuid()` / Python-side `uuid4()`), `created_at: datetime` (server default `now()`). No `owner_id` yet — the deliberate gap named above.
- `conversation_messages`: `id: UUID` (primary key), `conversation_id: UUID` (`ForeignKey("conversations.id")`, indexed), `role: str` (`"user"` or `"assistant"`), `content: Text`, `created_at: datetime` (server default `now()`). Ordered by `created_at` per conversation.

New Alembic migration: `create_conversations_and_conversation_messages_tables`.

## API Changes

`app/generation/schemas.py`:
- `GenerationQuery` gains `conversation_id: UUID | None = Field(default=None)`.
- `GenerationResponse` gains `conversation_id: UUID` (always populated — the caller's ID echoed back, or a newly server-generated one).

Server-generates every `conversation_id`; the client never picks one. If a `conversation_id` is provided but no matching `conversations` row exists, the router returns `404` (not a silent new conversation under that ID) — a not-found ID means a typo or stale reference, not something to paper over.

`app/generation/router.py`: catches a new `ConversationNotFoundError` (raised by `service.generate`) and maps it to `404`, alongside the existing catch-all → `503`.

## Query Rewriting

New `app/generation/rewrite.py`: `rewrite_query(query: str, history: list[ConversationMessage], llm_client: LLMClient) -> str`. Fixed instruction prompt: rephrase the latest question into a standalone version using the given history, without answering it; if already standalone, return unchanged. Reuses the same `LLMClient`/model as generation — no separate rewrite-model setting, keeping this YAGNI.

Only invoked when `conversation_id` is given **and** the loaded history is non-empty — a brand-new conversation's first turn costs nothing extra and behaves exactly like ERP-017 today. If the rewrite call itself raises, it propagates like any other LLM failure on this path (existing `503` contract) — no silent fallback to the raw query, since that would silently degrade retrieval without telling the caller.

The rewritten query is used for retrieval only. The original raw user text is what gets persisted as the `"user"` message and what the LLM sees verbatim in the generation prompt's conversation-history section.

## Generation Prompt Changes

`app/generation/prompt.py`'s `build_prompt` gains an optional `history: list[ConversationMessage] = []` parameter, rendered before the numbered context block (`User: ...` / `Assistant: ...` lines, oldest first). Citation numbering and the character-budget truncation of retrieved chunks are unchanged; history is not subject to the same truncation budget — it is already bounded by the fixed window (see below), which keeps its worst-case size predictable.

## History Window & Config

`GenerationSettings` gains `history_window_turns: int = 6` (meaning: the 6 most recent `conversation_messages` rows, i.e. up to 3 user/assistant pairs) — loaded newest-first then reversed to chronological order for both rewriting and the generation prompt. Bounds prompt growth regardless of conversation length; older turns are simply not surfaced, an accepted trade-off for this scope (full-history recall is not a requirement here).

## Service Flow (`app/generation/service.py`)

`generate(..., conversation_id: UUID | None = None, ...)`:
1. Open a session (`get_session_factory()`, same pattern as `retrieval.service.search`'s internal session).
2. If `conversation_id` given: load the conversation row (404 via `ConversationNotFoundError` if missing) and its last `history_window_turns` messages. If not given: no history, no rewrite, new conversation created later.
3. If history is non-empty: `rewritten_query = rewrite_query(query, history, llm_client)`. Else: `rewritten_query = query`.
4. `chunks = retrieval_search(rewritten_query, top_k, rerank=rerank, expand_sections=expand_sections)` — unchanged call, own internal session as today.
5. Empty `chunks` short-circuits to `NO_CONTEXT_ANSWER` as today — but the turn is still persisted (step 7), so an unanswerable follow-up remains part of the conversation's record rather than vanishing.
6. Otherwise: `build_prompt(query, chunks, settings.max_context_chars, history=history)` → `llm_client.generate(...)`.
7. Persist: if `conversation_id` was `None`, insert a new `conversations` row and flush to get its ID; insert the `"user"` message (raw `query`) and the `"assistant"` message (the answer); commit once, only after generation succeeded. On any earlier failure, nothing is committed — no dangling conversation or half-written turn.
8. Return `GenerationResponse(answer=..., citations=..., conversation_id=...)`.

## Error Handling

- Unknown `conversation_id` → `ConversationNotFoundError` → router `404`.
- Any LLM failure (rewrite or generation) → existing catch-all → router `503`, matching ERP-017's contract exactly. Nothing is committed in this case.

## Testing

- `app/generation/repository.py` (new, mirroring `app/ingestion/repository.py`'s style): tests against a real Postgres test database (existing CI/dev pattern) for `create_conversation`, `append_message`, `get_recent_messages`.
- `rewrite_query`: unit tests with an injected fake `LLMClient`, asserting the exact prompt shape and that it's *not* called when history is empty.
- `build_prompt`: new test cases for the `history` parameter (rendered before context, chronological order, absent when `history=[]`).
- `service.generate`: extended with `conversation_id`-present cases (new conversation, continuing conversation, unknown ID → `ConversationNotFoundError`, short-circuit-still-persists) using injected fakes, consistent with ERP-017's testing pattern; a regression test proves the no-`conversation_id` path is byte-for-byte unchanged from ERP-017.
- `router.py`: end-to-end tests for the new `404` case and a two-call sequence (first call omits `conversation_id`, second call reuses the returned one) asserting the second call's retrieval query differs from the raw text (i.e., rewriting actually ran).
- 90% coverage gate applies, per existing CI configuration.

## New Dependencies

None — reuses `ollama` (already present) for rewriting, and SQLAlchemy/Alembic (already present) for the new tables.
