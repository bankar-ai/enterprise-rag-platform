import uuid

import pytest

from app.auth.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_hash_password_round_trips():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_password_returns_false_for_not_a_valid_argon2_hash():
    # Finding 2 (final whole-branch review): the migration-seeded `system` user's
    # hashed_password is "!" (deliberately not a valid Argon2 hash, so it can never
    # authenticate) -- argon2-cffi raises InvalidHashError (a ValueError subclass, not
    # VerifyMismatchError) for this input, which used to propagate uncaught.
    assert verify_password("anything", "!") is False


def test_generate_refresh_token_is_unique_and_hash_is_deterministic():
    token_a = generate_refresh_token()
    token_b = generate_refresh_token()
    assert token_a != token_b
    assert hash_refresh_token(token_a) == hash_refresh_token(token_a)
    assert hash_refresh_token(token_a) != hash_refresh_token(token_b)


def test_access_token_round_trips(auth_settings):
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "admin", auth_settings)
    current_user = decode_access_token(token, auth_settings)
    assert current_user.id == user_id
    assert current_user.role == "admin"


def test_decode_access_token_rejects_garbage(auth_settings):
    with pytest.raises(InvalidTokenError):
        decode_access_token("not-a-real-token", auth_settings)


def test_decode_access_token_rejects_wrong_secret(auth_settings):
    from app.auth.config import AuthSettings

    token = create_access_token(uuid.uuid4(), "user", auth_settings)
    wrong_settings = AuthSettings(jwt_secret_key="a-different-secret", redis_url=auth_settings.redis_url)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, wrong_settings)
