"""OIDC protocol helpers: discovery, PKCE, authorization-code exchange, ID token verification.

Provider-agnostic by design -- every provider-specific value (endpoints, signing keys) comes
from the issuer's `.well-known/openid-configuration` discovery document and its JWKS, resolved
at request time, never hardcoded for a specific IdP. See the design spec
(`docs/superpowers/specs/2026-09-06-oidc-authentication-design.md`) for why this is implemented
directly against `httpx`/`joserfc` rather than Authlib's higher-level session-based integrations.

This module only speaks the OIDC wire protocol -- it has no knowledge of `users`/`oidc_identities`
or account-linking; that lives in `app/auth/service.py`.
"""

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from joserfc import jwt as joserfc_jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from app.auth.config import AuthSettings

logger = logging.getLogger(__name__)

_STATE_TOKEN_PURPOSE = "oidc_state"
_SCOPE = "openid email profile"
_ID_TOKEN_ALGORITHMS = ["RS256"]


class OidcDiscoveryError(Exception):
    """Raised when fetching or parsing the issuer's discovery document or JWKS fails."""


class OidcTokenExchangeError(Exception):
    """Raised when exchanging an authorization code for tokens fails or the response is malformed."""


class OidcTokenValidationError(Exception):
    """Raised when the returned ID token's signature, claims, or nonce fail validation."""


class InvalidOidcStateError(Exception):
    """Raised when the OAuth `state`/cookie pair fails validation.

    Covers a missing cookie, a bad signature, an expired cookie, a `state` value that doesn't
    match the cookie's own `state` claim, or any other tampering -- both CSRF-binding failures
    and PKCE-verifier/nonce recovery failures.
    """


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE `(code_verifier, code_challenge)` pair using the S256 method."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def generate_state_value() -> str:
    """Generate a random opaque anti-CSRF `state` value (round-tripped through the IdP)."""
    return secrets.token_urlsafe(24)


def create_oidc_cookie(state: str, code_verifier: str, nonce: str, settings: AuthSettings) -> str:
    """Sign `state` + `code_verifier` + `nonce` into a short-lived JWT held only in a cookie.

    This API has no server-side session, so this cookie is what stands in for one across the
    redirect round trip to the IdP and back. Critically, `code_verifier` lives *only* here, never
    in the OAuth `state`/`code` query parameters that travel through the browser's address bar,
    history, and any intermediate access logs -- putting the verifier in a URL-visible value would
    let anyone who can read those (not just a network attacker; ordinary proxy/server access logs
    routinely capture full request URLs) recover it *and* the authorization `code` from the very
    same channel, defeating PKCE's entire purpose. The cookie is `HttpOnly`/`Secure`/`SameSite=Lax`
    (set in `app/auth/router.py`), so an attacker forging a callback link to a victim's browser
    (login CSRF) cannot supply a matching cookie -- see `decode_oidc_cookie`'s CSRF check.

    Signed with the same secret used for access tokens, but a distinct `purpose` claim so it can
    never be confused with, or accepted as, a real access token.
    """
    payload = {
        "purpose": _STATE_TOKEN_PURPOSE,
        "state": state,
        "code_verifier": code_verifier,
        "nonce": nonce,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.oidc_state_expire_seconds),
    }
    return pyjwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_oidc_cookie(cookie_value: str, state: str, settings: AuthSettings) -> tuple[str, str]:
    """Verify the OIDC cookie and bind it to the callback's `state` query parameter.

    Returns `(code_verifier, nonce)`. Raises `InvalidOidcStateError` for any failure -- bad
    signature, expired, wrong `purpose`, missing claims, *or* a `state` that doesn't match the
    cookie's own `state` claim -- all treated identically, mirroring `decode_access_token`'s
    fail-closed style. The `state` match is the actual CSRF defense: the cookie can only have been
    set by our own `/login` response to the browser that requested it, so an attacker who crafts a
    callback URL with their own `code`/`state` cannot make it match a victim's browser's cookie
    (the victim either has no cookie at all, or has one from their own unrelated login attempt).
    """
    try:
        payload = pyjwt.decode(cookie_value, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("purpose") != _STATE_TOKEN_PURPOSE:
            raise InvalidOidcStateError("wrong token purpose")
        if payload["state"] != state:
            raise InvalidOidcStateError("state does not match the OIDC cookie")
        return payload["code_verifier"], payload["nonce"]
    except (pyjwt.PyJWTError, KeyError) as exc:
        raise InvalidOidcStateError from exc


def discover_metadata(issuer: str, settings: AuthSettings) -> dict[str, Any]:
    """Fetch and return the issuer's `.well-known/openid-configuration` discovery document."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        with httpx.Client(timeout=settings.oidc_http_timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            metadata: dict[str, Any] = response.json()
            return metadata
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("OIDC discovery failed for issuer %s", issuer)
        raise OidcDiscoveryError(issuer) from exc


def fetch_jwks(jwks_uri: str, settings: AuthSettings) -> dict[str, Any]:
    """Fetch and return the issuer's JSON Web Key Set."""
    try:
        with httpx.Client(timeout=settings.oidc_http_timeout_seconds) as client:
            response = client.get(jwks_uri)
            response.raise_for_status()
            jwks: dict[str, Any] = response.json()
            return jwks
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Fetching JWKS failed for %s", jwks_uri)
        raise OidcDiscoveryError(jwks_uri) from exc


def build_authorization_url(
    metadata: dict[str, Any],
    settings: AuthSettings,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """Build the full authorization URL to redirect the caller's browser to.

    `redirect_uri` and `client_id` always come from server-side config, never from the request,
    so this can't be turned into an open redirect or a client-substitution attack.
    """
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": _SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(params)}"


def exchange_code_for_tokens(
    metadata: dict[str, Any], settings: AuthSettings, code: str, code_verifier: str
) -> dict[str, Any]:
    """Exchange an authorization `code` for tokens at the issuer's token endpoint."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "code_verifier": code_verifier,
    }
    try:
        with httpx.Client(timeout=settings.oidc_http_timeout_seconds) as client:
            response = client.post(metadata["token_endpoint"], data=data)
            response.raise_for_status()
            tokens: dict[str, Any] = response.json()
            return tokens
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("OIDC token exchange failed")
        raise OidcTokenExchangeError from exc


def verify_id_token(
    id_token: str, metadata: dict[str, Any], settings: AuthSettings, nonce: str
) -> dict[str, Any]:
    """Verify `id_token`'s signature (via the issuer's JWKS) and standard claims, returning them.

    Checks signature, `iss`, `aud`, `exp` (via `joserfc`'s claims registry), and `nonce` (matched
    against the value from the decoded `state` token) -- the nonce match is what actually proves
    this specific ID token was issued in response to this specific authorization request.
    """
    jwks = fetch_jwks(metadata["jwks_uri"], settings)
    try:
        key_set = KeySet.import_key_set(jwks)  # type: ignore[arg-type]
        token = joserfc_jwt.decode(id_token, key_set, algorithms=_ID_TOKEN_ALGORITHMS)
        claims_registry = joserfc_jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": metadata["issuer"]},
            aud={"essential": True, "value": cast(str, settings.oidc_client_id)},
            exp={"essential": True},
        )
        claims_registry.validate(token.claims)
    except (JoseError, KeyError) as exc:
        logger.exception("ID token signature/claims validation failed")
        raise OidcTokenValidationError from exc

    if token.claims.get("nonce") != nonce:
        raise OidcTokenValidationError("nonce mismatch")
    return token.claims
