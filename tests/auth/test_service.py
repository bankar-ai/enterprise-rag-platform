import uuid

import pytest

from app.auth.service import (
    AccountDisabledError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    UserNotFoundError,
    list_all_users,
    login,
    logout,
    refresh_access_token,
    register_user,
    revoke_user_sessions,
    set_user_active_status,
)

# Mirrors the migration-seeded `system` user (alembic/versions/d456a2953c15_...): a fixed
# account with hashed_password="!" (deliberately not a valid Argon2 hash, so it can never
# authenticate). The test schema is built via `Base.metadata.create_all` (tests/conftest.py),
# which does not run the migration's data seed, so this row is inserted directly here.
_SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_SYSTEM_USER_EMAIL = "system@internal"
_SYSTEM_USER_HASH = "!"


def _ensure_system_user_seeded():
    from app.auth.models import UserRecord
    from app.core.db import get_session_factory

    session_factory = get_session_factory()
    with session_factory() as session:
        if session.get(UserRecord, _SYSTEM_USER_ID) is None:
            session.add(
                UserRecord(
                    id=_SYSTEM_USER_ID, email=_SYSTEM_USER_EMAIL, hashed_password=_SYSTEM_USER_HASH
                )
            )
            session.commit()


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


def test_list_all_users_includes_registered_user():
    register_user("list-test@example.com", "a-long-enough-password")
    emails = [user.email for user in list_all_users()]
    assert "list-test@example.com" in emails


def test_set_user_active_status_toggles_flag():
    user = register_user("disable-test@example.com", "a-long-enough-password")
    assert user.is_active is True

    disabled = set_user_active_status(user.id, False)
    assert disabled.is_active is False

    enabled = set_user_active_status(user.id, True)
    assert enabled.is_active is True


def test_set_user_active_status_raises_for_unknown_user():
    with pytest.raises(UserNotFoundError):
        set_user_active_status(uuid.uuid4(), False)


def test_disabled_user_cannot_login(auth_settings):
    user = register_user("login-disabled@example.com", "a-long-enough-password")
    set_user_active_status(user.id, False)

    with pytest.raises(AccountDisabledError):
        login("login-disabled@example.com", "a-long-enough-password", settings=auth_settings)


def test_disabling_user_after_login_blocks_refresh_but_not_the_existing_access_token(
    auth_settings,
):
    # Documents the accepted trade-off from the design: disabling a user blocks new logins and
    # refreshes immediately, but an already-issued access token is stateless and keeps working
    # until its own natural (short) expiry -- there is no per-request DB check.
    user = register_user("disable-after-login@example.com", "a-long-enough-password")
    tokens = login(
        "disable-after-login@example.com", "a-long-enough-password", settings=auth_settings
    )

    set_user_active_status(user.id, False)

    from app.auth.security import decode_access_token

    current_user = decode_access_token(tokens.access_token, auth_settings)
    assert current_user.id == user.id

    with pytest.raises(AccountDisabledError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_revoke_user_sessions_invalidates_refresh_token(auth_settings):
    user = register_user("revoke-sessions@example.com", "a-long-enough-password")
    tokens = login("revoke-sessions@example.com", "a-long-enough-password", settings=auth_settings)

    revoke_user_sessions(user.id)

    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_revoke_user_sessions_raises_for_unknown_user():
    with pytest.raises(UserNotFoundError):
        revoke_user_sessions(uuid.uuid4())


def test_login_against_seeded_system_user_raises_invalid_credentials(auth_settings):
    # Finding 2 (final whole-branch review): verify_password used to only catch
    # VerifyMismatchError, so authenticating against the seeded system user's "!"
    # (not-a-valid-Argon2-hash) placeholder raised an uncaught InvalidHashError instead of
    # the intended InvalidCredentialsError -- an uncaught exception (500 at the router) that
    # also doubled as a user-enumeration oracle (500 vs 401 confirms the account exists).
    _ensure_system_user_seeded()
    with pytest.raises(InvalidCredentialsError):
        login(_SYSTEM_USER_EMAIL, "anything-at-all", settings=auth_settings)
