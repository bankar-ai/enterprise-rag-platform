# Session — Admin user management

Date: 2026-09-05
Tickets Touched: ERP-027

## Decisions

- Classified as a Bounded change (brainstorming skill) rather than architectural: new endpoints inside the existing `app/auth/` module, reusing the `require_role("admin")` dependency ERP-026 had already scaffolded unused for exactly this. Short design agreed in chat, no spec file.
- Disabling a user does not add a per-request DB/cache check to `get_current_user` (the hot path). Instead: `login()` and `refresh_access_token()` both check `user.is_active` and raise `AccountDisabledError` (mapped to `403`); an already-issued access token keeps working until its own ≤30 min natural expiry. User explicitly chose this ("recommended option") over adding per-request enforcement.
- Force-revoking a user's sessions is a DB-only bulk `UPDATE` (`revoke_all_refresh_tokens_for_user`) — no per-token Redis cache writes, consistent with the cache being fast-path-only (the real check on refresh is always the Postgres row).

## Implementation Summary

- `app/auth/models.py`: `UserRecord.is_active: bool` (default `True`).
- New Alembic migration `cc12bb2f6bc7_add_is_active_to_users.py` (down_revision `d456a2953c15`, current head). Verified upgrade/downgrade/upgrade round-trip and an empty `alembic revision --autogenerate` diff against a real Postgres DB.
- `app/auth/repository.py`: `get_user_by_id`, `list_users`, `set_user_active`, `revoke_all_refresh_tokens_for_user`.
- `app/auth/service.py`: `login`/`refresh_access_token` now raise `AccountDisabledError` for a disabled account; new `list_all_users`, `set_user_active_status` (raises `UserNotFoundError`), `revoke_user_sessions` (raises `UserNotFoundError`).
- `app/auth/router.py`: new `admin_router` (`/admin/users`, `dependencies=[Depends(require_role("admin"))]`) with `GET`, `PATCH /{user_id}`, `POST /{user_id}/revoke-sessions`; `login`/`refresh` gained a `403` handler for `AccountDisabledError`.
- `app/main.py`: registered `admin_router`.
- `app/auth/schemas.py`: `UserResponse` gained `is_active`; new `UpdateUserActiveRequest`.
- Tests: `tests/auth/test_admin.py` (new, router-level), additions to `tests/auth/test_repository.py` and `tests/auth/test_service.py`, `tests/auth_helpers.py` gained `create_admin_and_get_headers` (direct-DB admin creation, since self-service admin registration still doesn't exist), and `tests/test_auth_required.py`'s no-token regression list gained the three new admin routes.
- Verified: `ruff` clean, `mypy --strict` clean (48 files), full suite 262 passed at 98.97% coverage, pre-commit (gitleaks + ruff) clean on all changed files.

## Blockers

None.

## Next Steps

Not yet committed or merged — sitting on `develop` locally, awaiting the user's go-ahead to commit/push/PR. Remaining backlog items (per `.ai/memory/current-state.md`, still deferred): admin cross-user data visibility, self-service admin account creation, external IdP/OAuth2/OIDC, per-tenant/partitioned FAISS index, Evaluation, Observability.
