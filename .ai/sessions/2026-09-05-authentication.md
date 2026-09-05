# Session — Authentication

Date: 2026-09-05
Tickets Touched: ERP-026

## Decisions

**Authentication model:** Local email/password identity with JWT access tokens and rotating refresh tokens, avoiding external IdP/OAuth2/OIDC (deferred). Per-user data isolation enforced through required `owner_id` ownership checks on documents and conversations (wrong-owner access returns 404, not 403, to avoid confirming resource existence). Retrieval-time isolation via oversample-then-filter approach (simpler at current scale; partitioned/per-tenant FAISS index deferred for scale-driven future review). Refresh-token revocation cached in Redis cache-aside style (never load-bearing), completing ADR-003's third named Redis use. Admin account creation deliberately not exposed via `POST /auth/register`; the first `admin` user created via seed/manual DB step. Four follow-ups explicitly deferred: per-tenant/partitioned FAISS index, external IdP/OAuth2/OIDC integration, admin user-management endpoints, self-service admin account creation.

## Implementation Summary

16 tasks completed across the ERP-026 branch:

**Tasks 1–13 (Core Implementation & Testing)**: Built `app/auth/` module with JWT/refresh-token flow, user/role data model, ownership checks across all existing routers (ingestion, retrieval, generation, conversations), migration with backfill to system user, Redis revocation cache. Updated cache keys in ERP-013/ERP-021 to include `owner_id` for per-user scoping. Achieved 229 tests passing, 99% coverage.

**Task 14 (Security Code Review)**: External code review identified and fixed a Critical security bug — `get_or_create_conversation` and `get_recent_messages` were missing ownership checks on client-supplied `conversation_id`, allowing a user to read another user's conversation history and inject messages (fixed commit `c51e17e`). Review process validated the approach before closure, demonstrating effective isolation verification.

**Task 15 (Final Verification)**: Confirmed 229 tests passing, 99% coverage, ruff/mypy/pre-commit all clean. Full suite green on real Postgres+Redis. No linter violations, no type errors.

**Task 16 (Documentation)**: Created `.ai/tickets/ERP-026.md` (ticket record matching ERP-024/025 format), updated `.ai/memory/current-state.md` (added ERP-026 bullet to "What Exists", removed blocking language from ERP-021/ERP-022, added four deferred follow-ups to "Next Planned Work"), created `.ai/sessions/2026-09-05-authentication.md` session log.

Verification approach: 16-task plan executed end-to-end; security-focused code review caught a real Critical bug; full test suite run with real services (Postgres, Redis, Ollama) confirmed behavior and isolation. No test mocks, no skipped checks.

## Blockers

None.

## Next Steps

1. Merge ERP-026 from `worktree-erp-026-authentication` to `develop` via PR.
2. Promote ERP-024/ERP-025/ERP-026 from `develop` to `main` in a single batched promotion PR.
3. Track the four deferred follow-ups (per-tenant FAISS index, external IdP/OAuth2/OIDC, admin user-management endpoints, self-service admin creation) as future work, now explicitly listed in `current-state.md`'s "Next Planned Work".
