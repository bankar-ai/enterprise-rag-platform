"""Password hashing and JWT/refresh-token helpers.

Password hashing uses Argon2id (via `argon2-cffi`) — OWASP's current first-choice
recommendation for password storage. Refresh tokens are opaque (`secrets.token_urlsafe`);
only their SHA-256 hash is ever persisted, so a leaked database dump can't be replayed
directly as a valid refresh token.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.auth.config import AuthSettings
from app.auth.schemas import CurrentUser

_password_hasher = PasswordHasher()
_REFRESH_TOKEN_BYTES = 32


class InvalidTokenError(Exception):
    """Raised when an access token's signature, expiry, or claims are invalid."""


def hash_password(password: str) -> str:
    """Hash `password` with Argon2id."""
    return _password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Return `True` if `password` matches `hashed_password`."""
    try:
        _password_hasher.verify(hashed_password, password)
    except VerifyMismatchError:
        return False
    return True


def generate_refresh_token() -> str:
    """Generate a new cryptographically random opaque refresh token."""
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def hash_refresh_token(raw_token: str) -> str:
    """Hash a raw refresh token for storage/lookup (never store the raw value)."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_access_token(user_id: uuid.UUID, role: str, settings: AuthSettings) -> str:
    """Create a short-lived signed JWT carrying `user_id` (`sub`) and `role`."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: AuthSettings) -> CurrentUser:
    """Validate `token`'s signature and expiry, returning the identity it carries.

    Raises `InvalidTokenError` for any failure (bad signature, expired, malformed claims) —
    callers should treat all of these identically as "not authenticated".
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return CurrentUser(id=uuid.UUID(payload["sub"]), role=payload["role"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError from exc
