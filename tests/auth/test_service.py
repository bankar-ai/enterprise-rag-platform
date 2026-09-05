import pytest

from app.auth.service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    login,
    logout,
    refresh_access_token,
    register_user,
)


def test_register_user_then_login_succeeds(auth_settings):
    register_user("service-test@example.com", "a-long-enough-password")

    tokens = login("service-test@example.com", "a-long-enough-password", settings=auth_settings)

    assert tokens.access_token
    assert tokens.refresh_token


def test_register_user_rejects_duplicate_email():
    register_user("dup-test@example.com", "a-long-enough-password")
    with pytest.raises(EmailAlreadyRegisteredError):
        register_user("dup-test@example.com", "another-password")


def test_login_rejects_wrong_password(auth_settings):
    register_user("wrongpw-test@example.com", "a-long-enough-password")
    with pytest.raises(InvalidCredentialsError):
        login("wrongpw-test@example.com", "not-the-password", settings=auth_settings)


def test_login_rejects_unknown_email(auth_settings):
    with pytest.raises(InvalidCredentialsError):
        login("nobody-test@example.com", "whatever", settings=auth_settings)


def test_refresh_rotates_token_and_old_token_becomes_invalid(auth_settings):
    register_user("refresh-flow@example.com", "a-long-enough-password")
    tokens = login("refresh-flow@example.com", "a-long-enough-password", settings=auth_settings)

    new_tokens = refresh_access_token(tokens.refresh_token, settings=auth_settings)
    assert new_tokens.refresh_token != tokens.refresh_token

    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_refresh_rejects_unknown_token(auth_settings):
    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token("not-a-real-refresh-token", settings=auth_settings)


def test_logout_revokes_token(auth_settings):
    register_user("logout-test@example.com", "a-long-enough-password")
    tokens = login("logout-test@example.com", "a-long-enough-password", settings=auth_settings)

    logout(tokens.refresh_token)

    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_logout_of_unknown_token_is_a_no_op():
    logout("not-a-real-refresh-token")
