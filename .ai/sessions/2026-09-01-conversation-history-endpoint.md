# Session — Conversation History Read Endpoint

Date: 2026-09-01
Tickets Touched: ERP-019

## Decisions

- Classified as a bounded task (small, well-scoped read endpoint over code that already exists: `ConversationRecord`/`ConversationMessageRecord`, `app/generation/repository.py`, the existing router pattern) — no design spec written, just a short in-chat design presented and approved before implementation.
- Full history, no pagination: conversations are expected to be small at this platform's scale, and the stated use case (a client reloading a conversation after a refresh) needs everything, not a windowed slice. Pagination was considered and rejected as premature.
- 404 for an unknown `conversation_id`, not 200-with-empty-list: a GET is retrieving something, not implicitly creating it (unlike `POST /generation/query`'s lenient get-or-create semantics). In practice a conversation either doesn't exist (404) or has ≥2 messages (`generate()` always creates the conversation row and its first user+assistant messages together in one transaction) — there's no real "exists but empty" case to handle awkwardly.
- New top-level `conversations_router` (prefix `/conversations`), separate from the existing `/generation` router — a conversation is its own resource, matching how `/ingestion`, `/retrieval`, and `/generation` are each already separate routers. Lives in the same `app/generation/router.py` file (no new module) since conversations are already owned by the `generation` package.
- New repository functions kept distinct from existing ones rather than overloading them: `get_conversation` (plain lookup, `None` on miss) vs. `get_or_create_conversation` (creates on miss — wrong semantics for a read path); `get_all_messages` (unlimited) vs. `get_recent_messages` (windowed, built for the generation prompt's bounded-history use case).

## Implementation Summary

Implemented via direct TDD in this session (no subagents — small enough for a single bounded task), on branch `erp-019-conversation-history-endpoint`, four commits:

- `feat: add read-only conversation repository functions` — `get_conversation`, `get_all_messages` in `app/generation/repository.py`, with tests against a real Postgres test database.
- `feat: add Message and ConversationHistoryResponse schemas` — new Pydantic models in `app/generation/schemas.py`.
- `feat: add get_conversation_history service function` — `app/generation/service.py` gains `get_conversation_history(conversation_id) -> ConversationHistoryResponse | None`.
- `feat: add GET /conversations/{id} endpoint` — new `conversations_router` in `app/generation/router.py`, registered in `app/main.py`; returns `200` with ordered history or `404`.

One environment hiccup along the way: the local Postgres/Redis dev containers had gone stale (created before the current `docker-compose.yml`'s port-mapping was in place, so `docker compose up -d` just restarted them without republishing ports), causing a connection timeout on the first test run. Fixed with `docker compose up -d --force-recreate postgres redis`; the `erp_test` database survived on the existing volume.

Full suite: 161 tests (up from 149), 99.59% coverage (gate 90%), mypy clean, ruff clean.

## Blockers

None.

## Next Steps

- Streaming generation responses remain the largest undelivered roadmap item.
- Retrieval/query-result cache, session/auth-token cache, CI required-checks, and the GIN-index/migration-locking cleanup all remain open, low-urgency follow-ups (unchanged by this session).
- Open a PR for `erp-019-conversation-history-endpoint` against `develop` once the user is ready to integrate it.
