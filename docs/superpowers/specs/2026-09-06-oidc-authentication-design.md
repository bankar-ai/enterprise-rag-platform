# OIDC Authentication — Design Spec

Status: Approved (decisions pre-made per ERP-032's brief; implementation-detail calls documented here)
Date: 2026-09-06
Related: `.ai/tickets/ERP-032.md`; depends on ERP-026 (`docs/superpowers/specs/2026-09-05-authentication-design.md`)

## Purpose

Add OIDC-based login as an **additional** authentication path alongside local email/password
(ERP-026), unblocking SSO for enterprise adoption (Google/Microsoft/Okta/self-hosted IdPs)
without touching the existing local flow.

## Decisions already made (not re-litigated here)

- **Library**: Authlib (BSD). Used for its `joserfc`-based JOSE primitives (JWKS import, ID
  token signature/claims verification) — the actual OAuth2/OIDC wire protocol (authorization
  URL construction, code exchange, discovery fetch) is implemented directly against `httpx`
  rather than Authlib's higher-level `OAuth`/`OAuth2Client` integrations, because those assume
  a server-side session (Starlette `SessionMiddleware` or similar) to stash the PKCE verifier
  and nonce between the login and callback requests — this is a stateless bearer-token API with
  no session concept, so that assumption doesn't fit (see "Stateless state/PKCE/nonce" below).
- **Provider-agnostic, config-driven**: no Google-specific code anywhere. Every provider detail
  (authorization endpoint, token endpoint, signing keys) comes from the issuer's
  `.well-known/openid-configuration` discovery document and its JWKS, resolved at request time
  from `AuthSettings`. Swapping to Entra ID/Okta/Keycloak later is a config change only.
  Google is the reference/test provider (free, zero new self-hosted infra).
- **Authorization Code + PKCE**, no implicit/hybrid flow — required for a public API client per
  OAuth 2.0 Security BCP.
- **New dependencies**: `authlib` (approved in the ticket brief) and `httpx` (already resolved
  transitively via the `httpx-sse`/test-client dependency graph; promoted to a direct
  dependency here because `app/auth/oidc.py` now imports it directly for discovery/token-exchange
  HTTP calls — not a new package being pulled into the environment, just declaring an existing
  transitive one as direct since application code now depends on it).

## Endpoint shapes

- **`GET /auth/oidc/{provider}/login`** — starts login. Validates `provider` against the single
  configured `AuthSettings.oidc_provider_name` (a 404 for any other value, and a 404 if OIDC
  isn't configured at all — both cases return the identical generic 404 so an unauthenticated
  caller can't distinguish "wrong provider name" from "OIDC not configured"). Fetches discovery
  metadata, generates a PKCE verifier/challenge pair and a nonce, packs them into a signed
  `state` token (see below), and returns `307 Temporary Redirect` to the IdP's real
  `authorization_endpoint` (never a client-influenced URL — see Security Review).
- **`GET /auth/oidc/{provider}/callback?code=...&state=...`** — completes login. Verifies
  `state`, exchanges `code` for tokens, verifies the ID token, resolves/links the user, and
  returns **the same `TokenResponse` JSON body** (`access_token` + `refresh_token`) that
  `POST /auth/login` returns — `200 OK`, not a redirect. There is no frontend in this repo to
  redirect back to, and returning JSON directly avoids the open-redirect surface a
  client-supplied post-login redirect target would otherwise introduce (see Security Review).

Both registered on a new `oidc_router` (`app/auth/router.py`), included in `app/main.py`
alongside the existing `auth_router`/`admin_router`. `POST /auth/login`, `/register`,
`/refresh`, `/logout` are untouched.

## State/PKCE/nonce: an HttpOnly cookie, not a load-bearing Redis store (and not the URL either)

Every existing Redis use in this repo (embedding cache, retrieval cache, refresh-token
revocation cache) is a cache-aside optimization: a Redis outage degrades to a slower/always-miss
fallback, never a hard failure (ADR-003). PKCE-verifier/state/nonce storage does **not** fit
that pattern — it is a required security control, not a performance optimization, so it can't be
allowed to silently degrade to "skip the check" if Redis is unavailable. Introducing a
*load-bearing* Redis use for this would be a new category of risk for this codebase (a Redis
outage would either block all OIDC logins or, worse, be tempting to fail open), so it was
rejected in favor of the browser itself carrying the state.

**An earlier version of this design put the PKCE `code_verifier` and `nonce` inside the OAuth
`state` query parameter itself** (a JWT signed with `AUTH_JWT_SECRET_KEY`), reasoning that since
`state` round-trips through the browser to the IdP and back unmodified, no server-side storage
would be needed at all. Self-review before implementation finished caught two real problems with
that: (1) it defeats PKCE's own purpose — the verifier and the authorization `code` would both
end up traveling through the exact same URL-visible channel (the callback's query string), so
anyone who can observe that URL (not just a network attacker; ordinary proxy/server access logs
routinely capture full request URLs) recovers *both* the code and the verifier needed to redeem
it, which is precisely what PKCE exists to prevent; and (2) with no per-browser binding at all, a
classic **login-CSRF** was possible: an attacker completes their own real OIDC login, obtains a
valid `(code, state)` pair for *their own* account, and tricks a victim's browser into loading
`GET /auth/oidc/google/callback?code=<attacker's code>&state=<attacker's state>` — since it's a
plain, unauthenticated `GET` with no session/cookie check at all, the victim's browser would
silently receive tokens for the *attacker's* account and (if a future frontend naively stored
whatever came back) could end up submitting the victim's own data into the attacker's account.

**The shipped design instead uses a short-lived, `HttpOnly`/`Secure`/`SameSite=Lax` cookie**
(`oidc_state`, scoped to the `/auth/oidc` path, set by `GET /auth/oidc/{provider}/login` and read
by the `/callback`), which fixes both problems at once:

- The cookie — not the URL — carries `code_verifier` and `nonce` (signed into a JWT with a
  `purpose: "oidc_state"` claim and a short expiry, `AUTH_OIDC_STATE_EXPIRE_SECONDS`, default
  300s, so it's distinguishable from a real access token and can't be replayed indefinitely).
  They never appear in the authorization URL, the callback URL, browser history, or any access
  log — restoring PKCE's actual guarantee: an attacker who only ever observes URLs (the
  code-interception threat PKCE defends against) still lacks the verifier.
- The OAuth `state` **query parameter** is now just a random opaque anti-CSRF token
  (`oidc.generate_state_value()`), with no sensitive payload. The cookie's own `state` claim must
  match it exactly (`decode_oidc_cookie`) before anything else proceeds. This is what blocks the
  login-CSRF scenario above: `HttpOnly`/`Secure`/`SameSite=Lax` means only our own `/login`
  response can set this cookie for this domain, so an attacker's crafted callback link can never
  supply a matching cookie in the victim's browser — the victim either has none (fails with "no
  cookie") or has one from their own unrelated login attempt (fails with "state doesn't match").
- `verify_id_token` separately confirms the returned ID token's `nonce` claim matches the nonce
  recovered from the cookie, which is what prevents ID token replay/injection specifically (the
  cookie/state match proves *this browser* started *this* flow; the nonce match proves *this
  specific* ID token was issued by the IdP in response to *this specific* authorization request).

FastAPI drops `Response` header mutations made before a raised `HTTPException`, so the cookie is
only explicitly cleared on the success path (`app/auth/router.py`'s `oidc_callback`); failure
paths rely on the cookie's own short `Max-Age` instead, which loses no real protection — the
underlying authorization `code` is single-use at the IdP regardless of whether our cookie
lingers. Tests exercising `TestClient` against this flow must use an `https://` `base_url`
(`tests/auth/test_router.py`), since httpx's cookie jar won't send a `Secure`-flagged cookie back
over a plain `http://` request — a fake-scheme detail specific to the test harness, not a change
to production behavior (which always runs behind TLS).

## Data model

New table, `oidc_identities` (Alembic migration, new revision on top of `cc12bb2f6bc7`):

- `id` (UUID, PK)
- `user_id` (FK → `users.id`)
- `provider` (str) — e.g. `"google"`
- `external_id` (str) — the IdP's `sub` claim
- `email` (str) — the email claimed at link time, kept for audit; not used for lookups after
  the initial link (lookups are always by `(provider, external_id)`, never by email, once linked)
- `created_at`
- Unique constraint on `(provider, external_id)` — one external identity maps to exactly one
  local user, enforced at the DB level, not just in application logic.

`users.hashed_password` becomes **nullable** (was `NOT NULL`): an OIDC-only user (no local
password ever set) has no password to hash. `login()` now explicitly checks
`user.hashed_password is None` before calling `verify_password`, so a `None` password can never
match anything — an OIDC-only account simply can't authenticate via `POST /auth/login` (correct:
they never set a password), and this can't be exploited as a distinct-error oracle since it
folds into the same generic `InvalidCredentialsError` as every other login failure.

A **separate identities table** (rather than adding `auth_provider`/`external_id` columns
directly to `users`) was chosen over the ticket's other suggested shape because it lets one
`users` row hold **both** a local password and one or more linked OIDC identities
simultaneously — required by the "local login keeps working unchanged" acceptance criterion,
which a single `auth_provider` slot on `users` couldn't represent once an existing local user
links an OIDC identity.

## Account linking decision

**When an OIDC login's claimed email matches an existing `users` row that isn't already linked
to this exact `(provider, external_id)`:**

- If the ID token asserts `email_verified: true` for that email → **auto-link**: create an
  `oidc_identities` row pointing at the existing user, and log that user in. This applies
  uniformly whether the existing row is a local-password account or was itself created via a
  different OIDC provider.
- If `email_verified` is `false`, or the claim is absent → **reject** with `409 Conflict`
  (`OidcAccountConflictError`), and do **not** create any identity row or issue tokens.

**Rationale:** `email_verified` is the OIDC-spec-defined signal that the IdP itself has confirmed
control of the mailbox (not just that the user typed an email into a form) — it's the standard
trust anchor recommended for exactly this kind of cross-provider account linking (per the OIDC
Core spec and OWASP's account-linking guidance). Relying on it, rather than blindly trusting any
claimed email, is what prevents the account-takeover scenario named in the ticket brief: an
attacker who registers an OIDC identity at some IdP using someone else's email address, where
that IdP either doesn't verify email ownership or the attacker exploited a gap in its
verification, cannot silently take over the existing local (or other-provider) account for that
email — the login is rejected instead of merged. This is checked per-login (not just once at
link time), so it degrades safely even if a given IdP is later found to have weaker verification
than assumed. No self-service "explicit linking" flow (e.g. requiring the caller to already
be authenticated and confirm the link) is built in this ticket — a rejected conflict simply
means the user keeps using their existing login method; building an explicit linking UI/flow is
a reasonable future follow-up but isn't needed to satisfy "explicitly decided and documented."

## Config (`app/auth/config.py`'s `AuthSettings`)

```
oidc_provider_name: str = "google"       # AUTH_OIDC_PROVIDER_NAME
oidc_issuer: str | None = None           # AUTH_OIDC_ISSUER
oidc_client_id: str | None = None        # AUTH_OIDC_CLIENT_ID
oidc_client_secret: str | None = None    # AUTH_OIDC_CLIENT_SECRET
oidc_redirect_uri: str | None = None     # AUTH_OIDC_REDIRECT_URI
oidc_state_expire_seconds: int = 300     # AUTH_OIDC_STATE_EXPIRE_SECONDS
oidc_http_timeout_seconds: float = 5.0   # AUTH_OIDC_HTTP_TIMEOUT_SECONDS
```

OIDC is considered "configured" only when `oidc_issuer`, `oidc_client_id`, `oidc_client_secret`,
and `oidc_redirect_uri` are all set — all four are `None` by default so a deployment that never
sets them gets a plain 404 on both OIDC endpoints, with zero behavior change otherwise. All
values load from env vars only, per CLAUDE.md; `.env.example` documents each with comments,
never real values.

## Testing / faking the IdP

No test ever performs a real network call to Google or any IdP:

- **Protocol-layer unit tests** (`tests/auth/test_oidc.py`): `discover_metadata`, `fetch_jwks`,
  and `exchange_code_for_tokens` are exercised against `httpx.Client(transport=httpx.MockTransport(...))`
  — httpx's built-in fake-transport mechanism, already a project dependency, so no new test-only
  dependency is needed. `verify_id_token` is exercised against a real self-signed RS256 keypair
  generated in-test (via `cryptography`, already pulled in transitively by `authlib`) so the
  actual signature/claims/nonce verification logic runs for real, just against a fake IdP's key
  rather than Google's.
- **Business-logic tests** (`tests/auth/test_service.py`): `complete_oidc_login`'s account
  linking/creation/conflict decisions are tested by monkeypatching `app.auth.oidc.discover_metadata`,
  `exchange_code_for_tokens`, and `verify_id_token` to return canned data — the same
  monkeypatch-the-network-boundary-function convention this repo already uses for
  `OllamaEmbeddingClient.embed` in `tests/retrieval/test_router.py` and
  `tests/ingestion` router tests.
- **Router-level tests** (`tests/auth/test_router.py`): full `TestClient` requests through
  `GET /auth/oidc/{provider}/login` and `/callback`, with the same monkeypatch approach, verify
  the end-to-end contract (redirect target shape, final `TokenResponse`, and every error status
  code) without any FastAPI dependency-override machinery — consistent with how the rest of the
  auth router's tests work.

## Error handling

| Condition | Exception | HTTP status |
|---|---|---|
| Unknown/unconfigured provider | `OidcNotConfiguredError` | 404 |
| Bad/expired/tampered `state` | `InvalidOidcStateError` | 400 |
| IdP rejected the code / unreachable | `OidcTokenExchangeError` | 502 |
| ID token signature/claims/nonce invalid | `OidcTokenValidationError` | 502 |
| Email conflict, not auto-linkable | `OidcAccountConflictError` | 409 |
| Resolved account is disabled | `AccountDisabledError` | 403 |

Mirrors ERP-026's existing convention: generic errors for anything that could otherwise become
an enumeration oracle, specific errors otherwise, and every non-re-raising `except` logs via
`logger.exception`.

## Deferred / not in scope

- Only Google is verified as a concrete reference provider; generic-provider support beyond
  Google is exercised only against the protocol spec (discovery + JWKS + code exchange), not
  against a second real IdP.
- No self-service "explicitly link this OIDC identity to my existing logged-in account" flow —
  see the account-linking rationale above.
- No refresh-token equivalent from the IdP is stored or used; once our own tokens are issued,
  the OIDC session at the IdP is irrelevant to us (matches this repo's existing refresh-token
  model, which is entirely local).
