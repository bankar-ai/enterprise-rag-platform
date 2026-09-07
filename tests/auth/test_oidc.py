"""Protocol-layer tests for app.auth.oidc: discovery, PKCE, code exchange, ID token verification.

No test performs a real network call -- discovery/JWKS/token-exchange HTTP calls are faked via
`httpx.MockTransport` (an existing project dependency's own built-in fake-transport mechanism),
and ID token verification is exercised against a real, in-test-generated RSA keypair rather than
a real IdP's key.
"""

import base64
import hashlib
import time
from unittest.mock import patch

import httpx
import jwt as pyjwt
import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from app.auth.oidc import (
    InvalidOidcStateError,
    OidcDiscoveryError,
    OidcTokenExchangeError,
    OidcTokenValidationError,
    build_authorization_url,
    create_oidc_cookie,
    decode_oidc_cookie,
    discover_metadata,
    exchange_code_for_tokens,
    fetch_jwks,
    generate_pkce_pair,
    generate_state_value,
    verify_id_token,
)

_ISSUER = "https://idp.example.com"
_CLIENT_ID = "test-client-id"


@pytest.fixture
def rsa_key():
    """A fresh in-test RSA keypair standing in for a real IdP's signing key."""
    return RSAKey.generate_key(2048, parameters={"kid": "test-key-1"}, private=True)


def _jwks_for(rsa_key: RSAKey) -> dict:
    return {"keys": [rsa_key.as_dict(private=False)]}


def _sign_id_token(rsa_key: RSAKey, claims: dict) -> str:
    header = {"alg": "RS256", "kid": "test-key-1"}
    return joserfc_jwt.encode(header, claims, rsa_key)


def _base_claims(nonce: str, **overrides) -> dict:
    claims = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "external-user-1",
        "email": "user@example.com",
        "email_verified": True,
        "nonce": nonce,
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return claims


_RealHttpxClient = httpx.Client


def _stub_httpx_client(handler):
    """Return a `patch("httpx.Client", ...)` context manager that fakes all HTTP via `handler`.

    Captures the *real* `httpx.Client` class at import time so the fake factory doesn't
    recursively call the patched name (`httpx.Client` is being replaced by this very factory).
    """
    return patch(
        "httpx.Client", lambda **kw: _RealHttpxClient(transport=httpx.MockTransport(handler), **kw)
    )


# --- PKCE ---------------------------------------------------------------------------------


def test_generate_pkce_pair_challenge_matches_verifier():
    code_verifier, code_challenge = generate_pkce_pair()
    expected_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
    assert code_challenge == expected_challenge


def test_generate_pkce_pair_is_random_each_call():
    first_verifier, _ = generate_pkce_pair()
    second_verifier, _ = generate_pkce_pair()
    assert first_verifier != second_verifier


# --- state value + OIDC cookie ---------------------------------------------------------------


def test_generate_state_value_is_random_each_call():
    assert generate_state_value() != generate_state_value()


def test_oidc_cookie_round_trips(auth_settings):
    state = generate_state_value()
    cookie = create_oidc_cookie(state, "a-verifier", "a-nonce", auth_settings)
    code_verifier, nonce = decode_oidc_cookie(cookie, state, auth_settings)
    assert code_verifier == "a-verifier"
    assert nonce == "a-nonce"


def test_decode_oidc_cookie_rejects_garbage(auth_settings):
    with pytest.raises(InvalidOidcStateError):
        decode_oidc_cookie("not-a-real-cookie", "some-state", auth_settings)


def test_decode_oidc_cookie_rejects_state_mismatch(auth_settings):
    # This is the actual CSRF defense: an attacker who crafts a callback URL with their own
    # `state` value cannot make it match a victim's browser's cookie (the cookie's own embedded
    # `state` claim only matches the value the *same* /login response generated).
    state = generate_state_value()
    cookie = create_oidc_cookie(state, "a-verifier", "a-nonce", auth_settings)
    with pytest.raises(InvalidOidcStateError):
        decode_oidc_cookie(cookie, "a-different-state-value", auth_settings)


def test_decode_oidc_cookie_rejects_expired_cookie(auth_settings):
    with patch("app.auth.oidc.datetime") as mock_datetime:
        from datetime import datetime, timedelta, timezone

        mock_datetime.now.return_value = datetime.now(timezone.utc) - timedelta(hours=1)
        state = generate_state_value()
        cookie = create_oidc_cookie(state, "verifier", "nonce", auth_settings)
    with pytest.raises(InvalidOidcStateError):
        decode_oidc_cookie(cookie, state, auth_settings)


def test_decode_oidc_cookie_rejects_wrong_purpose(auth_settings):
    # A real access token has no "code_verifier"/"nonce"/"purpose": "oidc_state" claims -- it
    # must never be accepted as an OIDC cookie, even though both are signed with the same secret.
    payload = {"purpose": "not_oidc_state", "state": "s", "code_verifier": "v", "nonce": "n"}
    token = pyjwt.encode(payload, auth_settings.jwt_secret_key, algorithm=auth_settings.jwt_algorithm)
    with pytest.raises(InvalidOidcStateError):
        decode_oidc_cookie(token, "s", auth_settings)


def test_decode_oidc_cookie_rejects_cookie_signed_with_different_secret(auth_settings):
    from app.auth.config import AuthSettings

    state = generate_state_value()
    cookie = create_oidc_cookie(state, "verifier", "nonce", auth_settings)
    wrong_settings = AuthSettings(
        jwt_secret_key="a-totally-different-secret", redis_url=auth_settings.redis_url
    )
    with pytest.raises(InvalidOidcStateError):
        decode_oidc_cookie(cookie, state, wrong_settings)


# --- discovery / JWKS -----------------------------------------------------------------------


def test_discover_metadata_fetches_well_known_document(auth_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{_ISSUER}/.well-known/openid-configuration"
        return httpx.Response(200, json={"issuer": _ISSUER, "authorization_endpoint": "https://x"})

    with _stub_httpx_client(handler):
        metadata = discover_metadata(_ISSUER, auth_settings)
    assert metadata["issuer"] == _ISSUER


def test_discover_metadata_raises_on_http_error(auth_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _stub_httpx_client(handler):
        with pytest.raises(OidcDiscoveryError):
            discover_metadata(_ISSUER, auth_settings)


def test_discover_metadata_raises_on_malformed_json(auth_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with _stub_httpx_client(handler):
        with pytest.raises(OidcDiscoveryError):
            discover_metadata(_ISSUER, auth_settings)


def test_fetch_jwks_raises_on_http_error(auth_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _stub_httpx_client(handler):
        with pytest.raises(OidcDiscoveryError):
            fetch_jwks("https://idp.example.com/jwks", auth_settings)


# --- authorization URL ------------------------------------------------------------------------


def test_build_authorization_url_uses_configured_client_and_redirect(auth_settings):
    metadata = {"authorization_endpoint": "https://idp.example.com/authorize"}
    settings = auth_settings.model_copy(
        update={"oidc_client_id": _CLIENT_ID, "oidc_redirect_uri": "https://app.example.com/callback"}
    )
    url = build_authorization_url(metadata, settings, "state-val", "nonce-val", "challenge-val")
    assert url.startswith("https://idp.example.com/authorize?")
    assert "client_id=test-client-id" in url
    assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fcallback" in url
    assert "code_challenge=challenge-val" in url
    assert "code_challenge_method=S256" in url
    assert "state=state-val" in url
    assert "nonce=nonce-val" in url


# --- token exchange -------------------------------------------------------------------------


def test_exchange_code_for_tokens_posts_expected_form(auth_settings):
    settings = auth_settings.model_copy(
        update={
            "oidc_client_id": _CLIENT_ID,
            "oidc_client_secret": "shh",
            "oidc_redirect_uri": "https://app.example.com/callback",
        }
    )
    metadata = {"token_endpoint": "https://idp.example.com/token"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert "grant_type=authorization_code" in body
        assert "code=auth-code" in body
        assert "code_verifier=verifier-val" in body
        return httpx.Response(200, json={"id_token": "fake-id-token", "access_token": "fake-access"})

    with _stub_httpx_client(handler):
        tokens = exchange_code_for_tokens(metadata, settings, "auth-code", "verifier-val")
    assert tokens["id_token"] == "fake-id-token"


def test_exchange_code_for_tokens_raises_on_error_response(auth_settings):
    metadata = {"token_endpoint": "https://idp.example.com/token"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with _stub_httpx_client(handler):
        with pytest.raises(OidcTokenExchangeError):
            exchange_code_for_tokens(metadata, auth_settings, "bad-code", "verifier-val")


# --- ID token verification -------------------------------------------------------------------


def test_verify_id_token_accepts_valid_token(auth_settings, rsa_key):
    settings = auth_settings.model_copy(update={"oidc_client_id": _CLIENT_ID})
    id_token = _sign_id_token(rsa_key, _base_claims("expected-nonce"))
    metadata = {"issuer": _ISSUER, "jwks_uri": "https://idp.example.com/jwks"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jwks_for(rsa_key))

    with _stub_httpx_client(handler):
        claims = verify_id_token(id_token, metadata, settings, "expected-nonce")
    assert claims["sub"] == "external-user-1"
    assert claims["email"] == "user@example.com"


def test_verify_id_token_rejects_nonce_mismatch(auth_settings, rsa_key):
    settings = auth_settings.model_copy(update={"oidc_client_id": _CLIENT_ID})
    id_token = _sign_id_token(rsa_key, _base_claims("actual-nonce"))
    metadata = {"issuer": _ISSUER, "jwks_uri": "https://idp.example.com/jwks"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jwks_for(rsa_key))

    with _stub_httpx_client(handler):
        with pytest.raises(OidcTokenValidationError):
            verify_id_token(id_token, metadata, settings, "expected-nonce")


def test_verify_id_token_rejects_wrong_issuer(auth_settings, rsa_key):
    settings = auth_settings.model_copy(update={"oidc_client_id": _CLIENT_ID})
    id_token = _sign_id_token(rsa_key, _base_claims("n", iss="https://attacker.example.com"))
    metadata = {"issuer": _ISSUER, "jwks_uri": "https://idp.example.com/jwks"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jwks_for(rsa_key))

    with _stub_httpx_client(handler):
        with pytest.raises(OidcTokenValidationError):
            verify_id_token(id_token, metadata, settings, "n")


def test_verify_id_token_rejects_wrong_audience(auth_settings, rsa_key):
    settings = auth_settings.model_copy(update={"oidc_client_id": _CLIENT_ID})
    id_token = _sign_id_token(rsa_key, _base_claims("n", aud="some-other-client"))
    metadata = {"issuer": _ISSUER, "jwks_uri": "https://idp.example.com/jwks"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jwks_for(rsa_key))

    with _stub_httpx_client(handler):
        with pytest.raises(OidcTokenValidationError):
            verify_id_token(id_token, metadata, settings, "n")


def test_verify_id_token_rejects_expired_token(auth_settings, rsa_key):
    settings = auth_settings.model_copy(update={"oidc_client_id": _CLIENT_ID})
    id_token = _sign_id_token(rsa_key, _base_claims("n", exp=int(time.time()) - 300))
    metadata = {"issuer": _ISSUER, "jwks_uri": "https://idp.example.com/jwks"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jwks_for(rsa_key))

    with _stub_httpx_client(handler):
        with pytest.raises(OidcTokenValidationError):
            verify_id_token(id_token, metadata, settings, "n")


def test_verify_id_token_rejects_signature_from_wrong_key(auth_settings, rsa_key):
    other_key = RSAKey.generate_key(2048, parameters={"kid": "test-key-1"}, private=True)
    settings = auth_settings.model_copy(update={"oidc_client_id": _CLIENT_ID})
    id_token = _sign_id_token(other_key, _base_claims("n"))
    metadata = {"issuer": _ISSUER, "jwks_uri": "https://idp.example.com/jwks"}

    def handler(request: httpx.Request) -> httpx.Response:
        # Serve rsa_key's (the "real" IdP's) JWKS, not other_key's -- simulating a forged token.
        return httpx.Response(200, json=_jwks_for(rsa_key))

    with _stub_httpx_client(handler):
        with pytest.raises(OidcTokenValidationError):
            verify_id_token(id_token, metadata, settings, "n")
