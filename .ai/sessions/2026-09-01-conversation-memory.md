# Session — Conversation Memory (ERP-018)

Date: 2026-09-01
Tickets Touched: ERP-018

## Decisions

- `conversation_id` is a stateless/stateful switch, not just a continuation token: omitted (or `null`), `POST /generation/query` is byte-for-byte identical to ERP-017's single-turn behavior — no DB session opened, no rewriting, nothing persisted, `response.conversation_id = null`. Provided, it's a client-supplied UUID (minted client-side, e.g. `uuid4()`) with get-or-create semantics — if no `conversations` row exists yet the server creates one on this call, otherwise it loads and continues that conversation's history. There is no server-generated-ID case and no "unknown ID" `404` — any client-supplied ID is valid to start or continue.
  - This resolved a genuine contradiction discovered mid-brainstorm in the first draft of the design spec: the API section promised statelessness-by-default while the Service Flow section described always persisting regardless of whether `conversation_id` was given. Making `conversation_id` itself the switch (rather than some other flag) resolved it cleanly and is what shipped.
- Query rewriting for retrieval reuses the existing `LLMClient`/model (`app/generation/rewrite.py`'s `rewrite_query`) — no separate rewrite-model setting, kept deliberately YAGNI. It only runs when `conversation_id` is given **and** the loaded history is non-empty, so a conversation's first turn costs nothing extra and behaves exactly like ERP-017.
- History is windowed to the last `history_window_turns` (default 6) messages — loaded newest-first, reversed to chronological order — for both rewriting and the generation prompt. Bounds prompt growth regardless of conversation length; older turns are simply not surfaced (accepted trade-off, not full-history recall).
- Both turns of an exchange (`"user"` + `"assistant"` messages) are persisted in a single transaction, only after generation succeeds — including the empty-retrieval short-circuit case, so an unanswerable follow-up still becomes part of the conversation's record rather than vanishing. Any earlier failure (retrieval, rewrite, or generation) commits nothing.
- The rewritten query is used for retrieval only — the original raw user text is what gets persisted as the `"user"` message and what the LLM sees verbatim in the prompt's history section.
- Streaming and a `GET /conversations/{id}` read endpoint stay explicitly out of scope — deferred, not abandoned. The client already has every answer it received in-band; a read endpoint is a follow-up ticket once a real client needs to reload history. Authentication/ownership of conversations is a separate, not-yet-built roadmap item; `conversation_id` is deliberately a bearer capability with no ownership check for now.
- No new dependency — reuses `ollama` (rewriting) and SQLAlchemy/Alembic (already present) for the new tables.

## Implementation Summary

New `app/generation/models.py` and `app/generation/repository.py`, plus modifications to the existing ERP-017 generation modules:

- `app/generation/models.py` (new) — `ConversationRecord` (`conversations` table: `id: UUID` primary key, client-supplied; `created_at`) and `ConversationMessageRecord` (`conversation_messages` table: `id`, `conversation_id` FK, `role`, `content`, `created_at`, and a `sequence` column added via follow-up migration — see below), sharing the existing `Base` from `app/ingestion/models.py`. Two Alembic migrations: the initial table creation, and a follow-up adding `sequence`.
- `app/generation/repository.py` (new) — `create_conversation`, `append_message`, `get_recent_messages`, mirroring `app/ingestion/repository.py`'s style; tested against a real Postgres test database, same pattern as the ingestion repository tests.
- `app/generation/rewrite.py` (new) — `rewrite_query(query, history, llm_client)`: fixed instruction prompt that rephrases the latest question into a standalone version using given history, or returns it unchanged if already standalone.
- `app/generation/schemas.py` (modified) — `GenerationQuery.conversation_id: UUID | None = None`; `GenerationResponse.conversation_id: UUID | None`; new `ConversationTurn`-related schema support for history rendering.
- `app/generation/prompt.py` (modified) — `build_prompt` gains an optional `history` parameter, rendered as `User: ...` / `Assistant: ...` lines before the numbered context block, oldest-first; not subject to the context character budget (already bounded by the fixed history window).
- `app/generation/service.py` (modified) — `generate(..., conversation_id: UUID | None = None, ...)` implements the full flow from the design spec: skip straight to ERP-017 behavior when `conversation_id` is `None`; otherwise load history, rewrite the query when history is non-empty, retrieve, generate, and persist both turns in one transaction after success.
- `app/generation/router.py` (modified) — passes `conversation_id` through to `service.generate`; no new exception-to-status mapping, same `200`/`503` outcomes as ERP-017.
- `app/generation/config.py` (modified) — `GenerationSettings.history_window_turns: int = 6`.

Ten commits on this branch (`erp-018-conversation-memory`):
- `7b8c055` docs: add ERP-018 conversation memory implementation plan
- `80cea4e` feat: add conversation/message ORM models and migration for ERP-018
- `73e4dfd` feat: add conversation repository for ERP-018
- `3eb731a` refactor: revert to brief-specified implementation for get_recent_messages
- `20733da` fix: add monotonic sequence column for reliable message ordering (ERP-018)
- `fa0c044` feat: add conversation_id and ConversationTurn schemas for ERP-018
- `9dcf101` feat: add LLM-based query rewriting for ERP-018
- `1aaa244` feat: add conversation history rendering to build_prompt for ERP-018
- `82f2966` feat: wire conversation memory into generate() orchestration for ERP-018
- `3b3ae70` feat: pass conversation_id through generation router for ERP-018

Full suite: 149 passed, 99.57% coverage (gate: 90%), mypy clean, ruff clean.

### Mid-implementation findings

Two genuine plan-text defects were found during implementation and fixed — both independently re-verified by task reviewers, not implementer mistakes:

1. **Non-deterministic message ordering (Task 2).** The implementation plan's own prescribed code for `get_recent_messages` ordered by `created_at`. Postgres's `now()` returns the *transaction-start* timestamp, and a single turn's user+assistant messages are inserted in one transaction — so both messages got the identical `created_at`, making their relative order non-deterministic under `ORDER BY created_at`. First attempted a revert back to the plan's literal `created_at`-ordering code (commit `3eb731a`) to confirm the defect was real and reproducible, then fixed it properly (commit `20733da`) by adding a monotonic `sequence` column (`Identity(always=True)`, mirroring the existing `ChunkRecord.vector_id` pattern in `app/ingestion/models.py`) via a follow-up Alembic migration, with `get_recent_messages` now ordering by `sequence` instead of `created_at`.
2. **Under-counted LLM call count in a plan-literal test (Task 7).** The plan's own literal test code for the two-call router test (first call starts a conversation, second call continues it and triggers rewriting) supplied only 2 canned LLM answers, but the real control flow needs 3 calls (one rewrite + one generation on the second turn, one generation on the first). Fixed by extending the test fixture to supply a third canned answer — no production-code change was needed.

## Blockers

None. All eight implementation tasks were reviewed and approved.

## Next Steps

- Open a PR for `erp-018-conversation-memory` into `develop`, summarizing the new `conversations`/`conversation_messages` tables and the `app/generation/*` module changes, and linking the design spec and this session log. Not merged — left for review.
- `GET /conversations/{id}` (a read endpoint for reloading conversation history) and streaming generation responses remain deferred, named as candidates for future tickets in the design spec.
