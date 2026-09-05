"""Business logic for registration, login, refresh-token rotation, and logout."""

import uuid
from datetime import datetime, timedelta, timezone

from app.auth.cache import RevocationCache, get_default_revocation_cache
from app.auth.config import AuthSettings, get_auth_settings
from app.auth.models import UserRecord
from app.auth.repository import (
    create_refresh_token,
    create_user,
    get_refresh_token_by_hash,
    get_user_by_email,
    get_user_by_id,
    list_users,
    revoke_all_refresh_tokens_for_user,
    revoke_refresh_token,
    set_user_active,
)
from app.auth.schemas import TokenResponse
from app.auth.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.core.db import get_session_factory


class EmailAlreadyRegisteredError(Exception):
    """Raised when registering with an email that's already taken."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials don't match any user."""


class InvalidRefreshTokenError(Exception):
    """Raised when a presented refresh token is missing, expired, or revoked."""


class AccountDisabledError(Exception):
    """Raised when a login or refresh is attempted for a disabled user."""


class UserNotFoundError(Exception):
    """Raised when an admin operation targets an unknown user id."""


def _as_aware_utc(value: datetime) -> datetime:
    """Attach UTC tzinfo to a naive `datetime` (the `expires_at` column is stored without one)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def register_user(email: str, password: str) -> UserRecord:
    """Register a new user with the `user` role. Raises `EmailAlreadyRegisteredError` on a duplicate."""
    session_factory = get_session_factory()
    with session_factory() as session:
        if get_user_by_email(session, email) is not None:
            raise EmailAlreadyRegisteredError(email)
        user = create_user(session, email, hash_password(password))
        session.commit()
        return user


def _issue_tokens(user: UserRecord, settings: AuthSettings) -> TokenResponse:
    session_factory = get_session_factory()
    access_token = create_access_token(user.id, user.role, settings)
    raw_refresh_token = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    with session_factory() as session:
        create_refresh_token(session, user.id, token_hash, expires_at)
        session.commit()
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh_token)


def login(
    email: str, password: str, settings: AuthSettings | None = None
) -> TokenResponse:
    """Exchange email + password for an access + refresh token pair.

    Raises `InvalidCredentialsError` for either an unknown email or a wrong password —
    deliberately the same error for both, to avoid confirming which emails are registered.
    Raises `AccountDisabledError` if the password matched but the account is disabled — safe
    to distinguish here since the password was already verified, so it reveals nothing new
    about which emails are registered.
    """
    settings = settings or get_auth_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_email(session, email)
        if user is None or not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError
        if not user.is_active:
            raise AccountDisabledError
    return _issue_tokens(user, settings)


def refresh_access_token(
    raw_refresh_token: str,
    settings: AuthSettings | None = None,
    revocation_cache: RevocationCache | None = None,
) -> TokenResponse:
    """Rotate `raw_refresh_token` for a new access + refresh token pair.

    Checks the revocation cache first for a fast rejection of a known-revoked token,
    then falls back to the authoritative Postgres check (missing, revoked, or expired
    all raise `InvalidRefreshTokenError`). Rotation revokes the presented token and
    caches that revocation with a TTL matching its remaining natural validity.
    """
    settings = settings or get_auth_settings()
    revocation_cache = revocation_cache or get_default_revocation_cache()
    token_hash = hash_refresh_token(raw_refresh_token)

    if revocation_cache.is_revoked(token_hash):
        raise InvalidRefreshTokenError

    session_factory = get_session_factory()
    with session_factory() as session:
        record = get_refresh_token_by_hash(session, token_hash)
        now = datetime.now(timezone.utc)
        if (
            record is None
            or record.revoked_at is not None
            or _as_aware_utc(record.expires_at) < now
        ):
            raise InvalidRefreshTokenError

        user = session.get(UserRecord, record.user_id)
        if user is None:
            raise InvalidRefreshTokenError
        if not user.is_active:
            raise AccountDisabledError

        remaining_ttl = max(0, int((_as_aware_utc(record.expires_at) - now).total_seconds()))
        revoke_refresh_token(session, record)
        session.commit()

    revocation_cache.mark_revoked(token_hash, remaining_ttl)
    return _issue_tokens(user, settings)


def logout(raw_refresh_token: str, revocation_cache: RevocationCache | None = None) -> None:
    """Revoke `raw_refresh_token`. A no-op if it's already unknown or already revoked."""
    revocation_cache = revocation_cache or get_default_revocation_cache()
    token_hash = hash_refresh_token(raw_refresh_token)

    session_factory = get_session_factory()
    with session_factory() as session:
        record = get_refresh_token_by_hash(session, token_hash)
        if record is None or record.revoked_at is not None:
            return
        now = datetime.now(timezone.utc)
        remaining_ttl = max(0, int((_as_aware_utc(record.expires_at) - now).total_seconds()))
        revoke_refresh_token(session, record)
        session.commit()

    revocation_cache.mark_revoked(token_hash, remaining_ttl)


def list_all_users() -> list[UserRecord]:
    """Return every registered user, ordered by creation time."""
    session_factory = get_session_factory()
    with session_factory() as session:
        return list_users(session)


def set_user_active_status(user_id: uuid.UUID, is_active: bool) -> UserRecord:
    """Enable or disable `user_id`'s account. Raises `UserNotFoundError` if unknown."""
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_id(session, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        set_user_active(session, user, is_active)
        session.commit()
        session.refresh(user)
        return user


def revoke_user_sessions(user_id: uuid.UUID) -> None:
    """Revoke every active refresh token belonging to `user_id`. Raises `UserNotFoundError` if unknown.

    A DB-only bulk revoke -- deliberately doesn't touch the revocation cache (which is a
    fast-path optimization, not authoritative; see `refresh_access_token`'s DB fallback check).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_id(session, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        revoke_all_refresh_tokens_for_user(session, user_id)
        session.commit()
