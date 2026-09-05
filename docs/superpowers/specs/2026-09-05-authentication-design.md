# Authentication — Design Spec

Status: Approved (brainstorming)
Date: 2026-09-05
Related roadmap item: `docs/roadmap.md` "Authentication"; `docs/architecture.md` Project Goals; ADR-003 (Data Layer & Caching Architecture)

## Purpose

Add authentication and authorization to the platform, with two goals:

1. **Access control** — every existing API endpoint requires a valid identity; no more anonymous access.
2. **Per-user data isolation** — each user only sees/queries their own uploaded documents and conversations. A role system (`admin` vs `user`) sits on top as a role *distinction* only — this ticket ships role enforcement (the two roles exist and are checked), not any elevated admin data-visibility privilege. Every ownership check in the codebase is a bare `owner_id == current_user.id` equality with no admin exception; an `admin` cross-user visibility bypass is explicitly deferred (see Future Follow-ups), not silently omitted.

This closes the "Authentication" gap named in `docs/roadmap.md` and `docs/architecture.md`'s Project Goals, and completes ADR-003's third named Redis use (session/auth-token cache), which was blocked on this ticket (see ERP-021/ERP-022 current-state notes).

## Scope

In scope:
- Local email/password identity (no external IdP/OAuth2/OIDC — deferred, see Future Follow-ups)
- JWT access tokens + rotating opaque refresh tokens, with Redis-cached revocation checks
- Role-based authorization: `admin`, `user`
- Per-user ownership on `documents` and `conversations` (chunks inherit ownership via their parent document)
- Retrieval-time isolation via oversample-then-filter (not a partitioned FAISS index — see Future Follow-ups)
- Migration + backfill of existing pre-auth data to a fixed `system` user

Out of scope (see Future Follow-ups):
- External IdP/OAuth2/OIDC integration
- Per-tenant/partitioned FAISS index
- Admin user-management endpoints (list/disable users, force-revoke sessions)
- Self-service admin account creation

## New Dependencies

- **`pyjwt`** — JWT encode/decode. Chosen over `python-jose` (heavier, has had unpatched CVEs historically).
- **`argon2-cffi`** — password hashing. OWASP currently recommends Argon2id as the first-choice password hash algorithm (bcrypt as fallback only); checked current guidance rather than defaulting to bcrypt from memory, per `CLAUDE.md`'s research-before-recommending rule.
- No new dependency for the bearer-token flow shape — FastAPI's built-in `OAuth2PasswordBearer` covers token extraction.

## Data Model

New tables (Postgres, via Alembic migration, sharing the existing `Base` from `app/ingestion/models.py`):

- **`users`**: `id` (UUID, PK), `email` (unique), `hashed_password`, `role` (`admin` | `user`), `created_at`.
- **`refresh_tokens`**: `id` (UUID, PK), `user_id` (FK → `users.id`), `token_hash`, `expires_at`, `revoked_at` (nullable), `created_at`. Only the hash of the opaque refresh token is stored, never the raw value.

Existing tables gain a column:

- **`documents.owner_id`** (FK → `users.id`, `NOT NULL`)
- **`conversations.owner_id`** (FK → `users.id`, `NOT NULL`)

`chunks` do not get their own `owner_id` — ownership is inherited through `chunks.document_id → documents.owner_id`.

### Migration & Backfill

Since `owner_id` must end up `NOT NULL` but existing rows predate the concept of ownership, the migration:

1. Creates `users` and `refresh_tokens`.
2. Adds `documents.owner_id` / `conversations.owner_id` as nullable.
3. Inserts (or upserts) a fixed `system` user row.
4. Backfills every existing `documents`/`conversations` row to the `system` user's ID.
5. Alters both columns to `NOT NULL`.

Downgrade drops the new tables and columns, restoring the pre-auth schema exactly. This mirrors the safe multi-step column-then-constraint pattern already used in ERP-025's migration.

## Auth Flow & Endpoints

New `app/auth/` module (`models.py`, `schemas.py`, `service.py`, `router.py`, `security.py`, `dependencies.py`), registered as a new top-level `/auth` router in `app/main.py`.

- **`POST /auth/register`** — email + password → creates a `users` row (role always defaults to `user`; there is no API path to create an `admin` — see Future Follow-ups).
- **`POST /auth/login`** — email + password → issues a short-lived access JWT (claims: `sub`=user_id, `role`; e.g. 30 min expiry) plus an opaque refresh token (only its hash persisted).
- **`POST /auth/refresh`** — presents a valid refresh token → rotates it (old token marked `revoked_at`, new one issued) and returns a new access JWT. Rotation-on-use means a replayed old refresh token is detectable (already revoked), a standard mitigation for refresh-token theft.
- **`POST /auth/logout`** — revokes the presented refresh token. Note: only the refresh token is revoked -- the short-lived access JWT already issued is not, and remains valid until its natural expiry (this is intended, not an oversight: access tokens are stateless-verified and were never checked against Redis/Postgres on each request).

**Protecting existing routes:** a `get_current_user` FastAPI dependency (via `OAuth2PasswordBearer`; validates JWT signature + expiry, loads the user) is added to every existing router (`ingestion`, `retrieval`, `generation`, `conversations`). A `require_role("admin")` dependency exists for future admin-only endpoints (none ship in this ticket).

**Session/auth-token cache (closes ADR-003's third Redis use):** revoked/rotated refresh-token hashes are cached in Redis with a TTL matching their remaining validity. `POST /auth/refresh` checks Redis first for a fast revocation rejection before falling back to Postgres — same cache-aside resilience pattern as ERP-013/ERP-021 (a Redis outage degrades to a Postgres-only check, not a hard failure).

## Data Isolation Enforcement

- **Ingestion:** `POST /ingestion/pdf` stamps the uploaded document's `owner_id` from the authenticated caller — never client-supplied. `GET /ingestion/jobs/{job_id}` returns `404` (not `403`) if the job's document belongs to a different user, to avoid confirming the ID exists.
- **Retrieval:** `app/retrieval/service.py`'s `search()` gains a required `owner_id` parameter. The existing Postgres hydration step (already fetching chunk rows for FAISS-returned vector IDs) adds a `WHERE document.owner_id = :owner_id` filter — this is the agreed "oversample-then-filter" approach (option a from brainstorming): correct and simple at current scale, though recall can degrade once a single user's chunks are a small fraction of a large shared index (see Future Follow-ups). The existing 4x oversample multiplier (ERP-014) is unchanged but now also absorbs isolation filtering, not just RRF fusion.
- **Generation:** `POST /generation/query` and `POST /generation/query/stream` pass the caller's `owner_id` through to `search()` unchanged in every other respect.
- **Conversations:** `conversations`/`conversation_messages` get the same ownership check as ingestion jobs — `GET /conversations/{id}` returns `404` for another user's conversation.
- **Retrieval cache key (ERP-021):** must be extended to include `owner_id`. This is a required fix, not optional — the existing cache key only hashes `(query, top_k, rerank, expand_sections)`, so without this change User A's cached results could leak to User B issuing the same query text.

## Error Handling

- Invalid/expired JWT → `401`.
- Missing/insufficient role → `403`.
- Wrong-owner resource access (job, conversation) → `404`, not `403` (avoids confirming the resource exists for another user).
- Duplicate email on register → `409`.

All follow the existing "raise meaningful exception, log unexpected failures" convention from `docs/engineering-guidelines.md` — no new error-handling patterns introduced.

## Testing

- New `tests/auth/`: register/login/refresh/logout, token validation, role enforcement. Target the existing `--cov-fail-under=90` gate.
- Every existing router's test suite (`tests/ingestion`, `tests/retrieval`, `tests/generation`) needs an authenticated-client test fixture.
- New cross-owner-isolation tests: verify User B cannot see or query User A's documents/conversations.
- Consistent with the project's existing convention of running real Postgres + Redis in CI (already true for `tests/ingestion`, `tests/embedding`, `tests/retrieval`) rather than mocking them — these are integration tests by the project's own established pattern.

## Deferred / Future Follow-ups

Logged explicitly per user instruction during brainstorming (2026-09-05), so these are found quickly when revisited:

1. **Per-tenant/partitioned FAISS index** — today's oversample-then-filter isolation degrades recall under real multi-tenant scale. Revisit once usage numbers justify a partitioned/per-tenant vector index. Also relevant to `docs/roadmap.md`'s still-open "Multi-document Retrieval" item.
2. **External IdP / OAuth2 / OIDC integration** — deferred; local email+password is the v1 identity model.
3. **Admin user-management endpoints** — list/disable users, force-revoke sessions. Only role *enforcement* (`admin` vs `user`) ships in this ticket; no admin-facing API surface yet.
4. **Self-service admin account creation** — deliberately not exposed via `POST /auth/register`; the first `admin` user is created via a seed/manual DB step.
5. **Admin cross-user data visibility** — distinct from item 3 above (which is about missing API surface): no code path lets an `admin` see or manage another user's documents/conversations today; every ownership check is a bare `owner_id` equality with no role-based bypass. Implementing this bypass is deferred rather than added at the tail end of this security feature, since untested privilege-escalation code is worse than an explicitly documented gap.

This list should be mirrored into `.ai/memory/current-state.md`'s "Next Planned Work" once this ticket is implemented and merged.
