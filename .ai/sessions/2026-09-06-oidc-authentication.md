# Session — OIDC Authentication (ERP-032)

Date: 2026-09-06
Tickets Touched: ERP-032

## Decisions

- **Library**: Authlib (BSD), specifically its `joserfc` JOSE primitives for JWKS import and ID
  token signature/claims verification. The actual OAuth2/OIDC wire protocol (discovery,
  authorization URL, code exchange) is hand-implemented against `httpx` rather than Authlib's
  higher-level `OAuth`/`OAuth2Client` integrations, because those assume a server-side session
  to stash the PKCE verifier/nonce -- this API has none. `httpx` was promoted from a dev-only
  transitive dependency to a direct one, since `app/auth/oidc.py` now imports it directly.
- **Provider-agnostic, Google-as-reference**: every provider detail (endpoints, signing keys)
  comes from the issuer's `.well-known/openid-configuration` discovery document, resolved at
  request time from `AuthSettings`. No Google-specific code anywhere.
- **Data model**: a new `oidc_identities` table (not `auth_provider`/`external_id` columns on
  `users`), so one user row can hold both a local password and one or more linked OIDC
  identities at once -- required for local login to keep working unchanged after linking.
  `users.hashed_password` became nullable for OIDC-only users.
- **Account linking decision**: an OIDC login whose email matches an existing user is
  auto-linked only when the ID token asserts `email_verified: true`; otherwise it's rejected
  with `409 Conflict` (`OidcAccountConflictError`), creating nothing. This is what prevents an
  attacker who controls an OIDC identity with an unverified claim to someone else's email from
  taking over that person's existing account.
- **Security self-review caught and fixed a real design flaw before shipping**: the first draft
  packed the PKCE `code_verifier` and `nonce` into the OAuth `state` query parameter itself
  (reasoning: no server session, so the browser round-trip is the only channel). This was wrong
  on two counts -- (1) it put the verifier in the same URL-visible channel as the authorization
  `code`, defeating PKCE's entire purpose (anyone who can read that URL, e.g. via ordinary
  access logs, gets both), and (2) with no per-browser binding at all, an attacker could
  complete their own OIDC login and CSRF the resulting `(code, state)` into a victim's browser,
  silently authenticating the victim's session as the attacker's account (login CSRF). Fixed by
  moving `code_verifier`/`nonce` into a short-lived `HttpOnly`/`Secure`/`SameSite=Lax` cookie
  (`oidc_state`, scoped to `/auth/oidc`), with `state` reduced to a plain opaque anti-CSRF token
  that must match the cookie's own embedded `state` claim. See the design spec's "State/PKCE/
  nonce" section for the full before/after.

## Implementation Summary

- `app/auth/oidc.py` (new): OIDC protocol helpers -- PKCE pair generation, the `oidc_state`
  cookie JWT (create/decode), discovery, JWKS fetch, authorization URL building, code exchange,
  and ID token verification (signature via JWKS, `iss`/`aud`/`exp`/`nonce` claims).
- `app/auth/config.py`: `AuthSettings` gained `oidc_provider_name`/`oidc_issuer`/
  `oidc_client_id`/`oidc_client_secret`/`oidc_redirect_uri`/`oidc_state_expire_seconds`/
  `oidc_http_timeout_seconds`, all env-loaded, all `None`/defaulted so an unconfigured
  deployment sees zero behavior change.
- `app/auth/models.py`: new `OidcIdentityRecord` (`oidc_identities` table, unique on
  `(provider, external_id)`); `UserRecord.hashed_password` is now nullable.
- `app/auth/repository.py`: `create_oidc_user`, `get_oidc_identity`, `create_oidc_identity`.
- `app/auth/service.py`: `start_oidc_login` (returns authorization URL + cookie value + max-age),
  `complete_oidc_login` (validates cookie/state, exchanges code, verifies ID token, resolves/
  links/creates the user via `_resolve_oidc_user`, issues the same JWT+refresh-token pair local
  login issues). `login()` now rejects a `None` `hashed_password` (OIDC-only accounts) with the
  same generic `InvalidCredentialsError` as any other login failure.
- `app/auth/router.py`: new `oidc_router` — `GET /auth/oidc/{provider}/login` (307 redirect,
  sets the `oidc_state` cookie) and `GET /auth/oidc/{provider}/callback` (200 JSON
  `TokenResponse`, clears the cookie on success). `POST /auth/login`/`register`/`refresh`/
  `logout` are byte-for-byte unchanged.
- `alembic/versions/dd26f4e8c54f_...py`: new migration (nullable `hashed_password` +
  `oidc_identities` table), verified upgrade/downgrade both ways with an empty autogenerate
  diff after upgrading.
- `.env.example`: documented (commented-out) OIDC env vars, no real values.
- Tests: `tests/auth/test_oidc.py` (new, protocol-layer, real self-signed RSA keys +
  `httpx.MockTransport`, zero real network), plus additions to `test_repository.py`,
  `test_service.py`, `test_router.py`, `test_config.py` covering new-user creation, identity
  reuse, verified-email auto-linking (and that local login keeps working after), the
  unverified-email conflict rejection, disabled-account handling, provider mismatch/
  unconfigured 404s, missing/mismatched-cookie CSRF rejection, and a full router-level round
  trip including using the issued token against a real protected endpoint.

## Blockers

None.

## Next Steps

- Only Google is a concrete reference provider; generic-provider support beyond Google is
  exercised only against the OIDC/JWKS protocol spec, not a second real IdP (deferred, per the
  design spec).
- No self-service "link this OIDC identity to my existing logged-in account" flow -- a rejected
  conflict just means the user keeps using their existing login method.
- `.ai/memory/current-state.md` and `.ai/tickets/ERP-032.md` updated to close this out.
