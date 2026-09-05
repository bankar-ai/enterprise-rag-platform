# Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local email/password authentication with JWT access + rotating refresh tokens, `admin`/`user` role authorization, and per-user data isolation (documents, chunks-via-document, conversations) across every existing router.

**Architecture:** A new `app/auth/` feature module (models, schemas, security, repository, cache, service, dependencies, router) follows the exact structure of `app/retrieval/` and `app/generation/`. Every existing router gains a `get_current_user` dependency; `owner_id` is threaded from the authenticated caller down through each service's existing call chain (ingestion jobs, retrieval `search()`, generation, conversations) to a Postgres-level filter. Retrieval isolation uses oversample-then-filter (existing 4x oversample multiplier absorbs it), not a partitioned FAISS index.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 + Alembic, Postgres, Redis (cache-aside only), `pyjwt` (new), `argon2-cffi` (new).

**Spec:** `docs/superpowers/specs/2026-09-05-authentication-design.md`

## Global Constraints

- Never hardcode secrets — `AuthSettings.jwt_secret_key` has **no default**, must come from the `AUTH_JWT_SECRET_KEY` env var (fails fast if unset), per `CLAUDE.md`.
- `mypy --strict` scoped to `app/`; every new/modified function needs full type hints.
- Ruff `select = ["E", "F", "I", "B", "D", "BLE", "PGH"]`, google-convention docstrings, no blanket `except Exception`/`# type: ignore`/`# noqa`.
- `pytest-cov --cov-fail-under=90` — every new module needs tests to match.
- New dependencies (`pyjwt`, `argon2-cffi`) added via `uv add`, never `pip install`.
- No business logic in routes — routes validate + call the service layer only.
- Redis is never load-bearing (ADR-003) — the refresh-token revocation cache degrades to "not cached as revoked" on any Redis error, falling back to the authoritative Postgres check.
- Wrong-owner resource access returns `404`, not `403` (avoids confirming another user's resource exists) — auth/role failures still return `401`/`403`.

---

### Task 1: Add auth dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `pyjwt` and `argon2-cffi` importable as `jwt` and `argon2`, available to every later task.

- [ ] **Step 1: Add the dependencies**

Run:
```bash
uv add pyjwt argon2-cffi
```

- [ ] **Step 2: Verify they import**

Run: `uv run python -c "import jwt, argon2; print('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pyjwt and argon2-cffi for authentication (ERP-026)"
```

---

### Task 2: Auth settings

**Files:**
- Create: `app/auth/__init__.py` (empty)
- Create: `app/auth/config.py`
- Test: `tests/auth/__init__.py` (empty)
- Test: `tests/auth/conftest.py`
- Test: `tests/auth/test_config.py`

**Interfaces:**
- Produces: `AuthSettings` (fields: `jwt_secret_key: str`, `jwt_algorithm: str = "HS256"`, `access_token_expire_minutes: int = 30`, `refresh_token_expire_days: int = 30`, `redis_url: str = "redis://localhost:6379/0"`, `redis_socket_timeout_seconds: float = 2.0`), `get_auth_settings() -> AuthSettings` (`lru_cache`d). Env prefix `AUTH_`.

- [ ] **Step 1: Write `tests/auth/conftest.py`**

```python
"""Shared fixtures for auth tests: a fixed test-only JWT secret and a dedicated Redis DB."""

import os

os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-only-secret-do-not-use-in-production")
os.environ.setdefault("AUTH_REDIS_URL", "redis://localhost:6379/3")

import pytest  # noqa: E402
import redis  # noqa: E402

from app.auth.config import AuthSettings  # noqa: E402


@pytest.fixture
def auth_settings() -> AuthSettings:
    """`AuthSettings` pointed at the dedicated test Redis logical DB."""
    return AuthSettings(
        jwt_secret_key=os.environ["AUTH_JWT_SECRET_KEY"],
        redis_url=os.environ["AUTH_REDIS_URL"],
    )


@pytest.fixture(autouse=True)
def _flush_test_redis_db(auth_settings: AuthSettings):
    """Flush the test-only Redis logical DB before and after every auth test."""
    client = redis.Redis.from_url(auth_settings.redis_url)
    client.flushdb()
    yield
    client.flushdb()
```

- [ ] **Step 2: Write the failing test — `tests/auth/test_config.py`**

```python
import pytest
from pydantic import ValidationError

from app.auth.config import AuthSettings, get_auth_settings


def test_settings_require_jwt_secret_key(monkeypatch):
    monkeypatch.delenv("AUTH_JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        AuthSettings()


def test_settings_have_expected_defaults(auth_settings):
    assert auth_settings.jwt_algorithm == "HS256"
    assert auth_settings.access_token_expire_minutes == 30
    assert auth_settings.refresh_token_expire_days == 30


def test_get_auth_settings_is_cached():
    assert get_auth_settings() is get_auth_settings()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 4: Write `app/auth/config.py`**

```python
"""Authentication settings, loaded from environment variables.

`jwt_secret_key` has no default — it must come from `AUTH_JWT_SECRET_KEY` — since a
hardcoded signing secret would let anyone forge access tokens (see CLAUDE.md's
never-hardcode-secrets rule).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Configuration for JWT signing, token lifetimes, and the revocation cache.

    Overridable via `AUTH_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = 2.0


@lru_cache
def get_auth_settings() -> AuthSettings:
    """Return the process-wide cached `AuthSettings` instance."""
    return AuthSettings()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add app/auth/__init__.py app/auth/config.py tests/auth/__init__.py tests/auth/conftest.py tests/auth/test_config.py
git commit -m "feat: add AuthSettings (ERP-026)"
```

---

### Task 3: Auth ORM models + migration

**Files:**
- Create: `app/auth/models.py`
- Modify: `alembic/versions/<new>_create_users_and_refresh_tokens.py` (generated via `alembic revision`)

**Interfaces:**
- Produces: `UserRecord` (`id: uuid.UUID` PK, `email: str` unique, `hashed_password: str`, `role: str` default `"user"`, `created_at: datetime`), `RefreshTokenRecord` (`id: uuid.UUID` PK, `user_id: uuid.UUID` FK→`users.id`, `token_hash: str` unique, `expires_at: datetime`, `revoked_at: datetime | None`, `created_at: datetime`).

- [ ] **Step 1: Write `app/auth/models.py`**

```python
"""SQLAlchemy ORM models for users and refresh tokens."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class UserRecord(Base):
    """A registered user, identified by email."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    hashed_password: Mapped[str]
    role: Mapped[str] = mapped_column(default="user")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefreshTokenRecord(Base):
    """A single issued refresh token. Only its hash is ever persisted, never the raw value."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True, index=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

- [ ] **Step 2: Verify the model imports and the test schema builds**

Run: `uv run python -c "from app.auth.models import UserRecord, RefreshTokenRecord; print('ok')"`
Expected: prints `ok`

Since `tests/conftest.py`'s `_database_schema` fixture calls `Base.metadata.create_all(engine)`, and `UserRecord`/`RefreshTokenRecord` share `Base` (imported from `app.ingestion.models`), just importing `app.auth.models` anywhere before that fixture runs is enough to register the tables. Add the import to `tests/conftest.py`:

Modify `tests/conftest.py` — add after the existing `from app.ingestion.models import Base` line:
```python
from app.auth.models import RefreshTokenRecord, UserRecord  # noqa: E402, F401
```

- [ ] **Step 3: Generate the Alembic migration**

Run: `uv run alembic revision --autogenerate -m "create users and refresh tokens tables"`
Expected: a new file under `alembic/versions/` with `upgrade()` creating `users` and `refresh_tokens` (matching the model above) and `downgrade()` dropping them in reverse order. Open the generated file and confirm it matches — autogenerate output for a straightforward `create_table` needs no hand-editing here (contrast with Task 5's migration, which does).

- [ ] **Step 4: Verify the migration runs cleanly against a real Postgres**

Run: `docker compose up -d postgres` (if not already running), then:
```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```
Expected: all three commands succeed with no errors.

- [ ] **Step 5: Commit**

```bash
git add app/auth/models.py tests/conftest.py alembic/versions/
git commit -m "feat: add users and refresh_tokens tables (ERP-026)"
```

---

### Task 4: Password hashing + JWT/refresh-token helpers

**Files:**
- Create: `app/auth/security.py`
- Test: `tests/auth/test_security.py`

**Interfaces:**
- Consumes: `AuthSettings` (Task 2).
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, hashed_password: str) -> bool`, `generate_refresh_token() -> str`, `hash_refresh_token(raw_token: str) -> str`, `create_access_token(user_id: uuid.UUID, role: str, settings: AuthSettings) -> str`, `decode_access_token(token: str, settings: AuthSettings) -> CurrentUser` (raises `InvalidTokenError`), `InvalidTokenError` (exception class). `CurrentUser` is defined in Task 5 (schemas) — this task defines `InvalidTokenError` and imports `CurrentUser` from `app.auth.schemas`, so Task 5 must land its `CurrentUser`/`Role` definitions first if run out of order; as planned here, do Task 5 before Task 4's `decode_access_token`, or inline a minimal `CurrentUser` stub in this task and let Task 5 supersede it. To avoid ordering hazards, this task defines `CurrentUser` itself in `app/auth/schemas.py` as part of Step 4 below (Task 5 then extends `app/auth/schemas.py` with the remaining request/response models).

- [ ] **Step 1: Write the failing test — `tests/auth/test_security.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_security.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.security'`

- [ ] **Step 3: Write `app/auth/schemas.py` (minimal slice needed by this task)**

```python
"""Pydantic schemas for the auth API's requests, responses, and resolved identity."""

import uuid
from typing import Literal

Role = Literal["admin", "user"]

from pydantic import BaseModel  # noqa: E402


class CurrentUser(BaseModel):
    """The authenticated caller, resolved from a validated access token."""

    id: uuid.UUID
    role: Role
```

- [ ] **Step 4: Write `app/auth/security.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_security.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add app/auth/security.py app/auth/schemas.py tests/auth/test_security.py
git commit -m "feat: add password hashing and JWT helpers (ERP-026)"
```

---

### Task 5: Auth request/response schemas

**Files:**
- Modify: `app/auth/schemas.py`
- Test: `tests/auth/test_schemas.py`

**Interfaces:**
- Consumes: `Role`, `CurrentUser` (Task 4).
- Produces: `RegisterRequest`, `UserResponse`, `LoginRequest`, `TokenResponse`, `RefreshRequest`, `LogoutRequest`.

- [ ] **Step 1: Write the failing test — `tests/auth/test_schemas.py`**

```python
import pytest
from pydantic import ValidationError

from app.auth.schemas import LoginRequest, RegisterRequest, TokenResponse


def test_register_request_rejects_short_password():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password="short")


def test_register_request_accepts_valid_input():
    request = RegisterRequest(email="a@example.com", password="a-long-enough-password")
    assert request.email == "a@example.com"


def test_login_request_requires_both_fields():
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com")


def test_token_response_defaults_token_type_to_bearer():
    response = TokenResponse(access_token="a", refresh_token="b")
    assert response.token_type == "bearer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'RegisterRequest'`

- [ ] **Step 3: Rewrite `app/auth/schemas.py` in full**

```python
"""Pydantic schemas for the auth API's requests, responses, and resolved identity.

`email` fields are plain `str` with a lightweight shape check, not Pydantic's `EmailStr` —
`EmailStr` requires the separate `email-validator` package, and full RFC email validation
isn't worth a new dependency here (an invalid email just fails at registration/login with
no matching user, which is already handled).
"""

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["admin", "user"]

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email_shape(value: str) -> str:
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("must be a valid email address")
    return value


class CurrentUser(BaseModel):
    """The authenticated caller, resolved from a validated access token."""

    id: uuid.UUID
    role: Role


class RegisterRequest(BaseModel):
    """A new-user registration request. Always registers with the `user` role."""

    email: str
    password: str = Field(min_length=8)

    _validate_email = field_validator("email")(_validate_email_shape)


class UserResponse(BaseModel):
    """A registered user's public profile."""

    id: uuid.UUID
    email: str
    role: Role


class LoginRequest(BaseModel):
    """An email + password login request."""

    email: str
    password: str

    _validate_email = field_validator("email")(_validate_email_shape)


class TokenResponse(BaseModel):
    """An issued access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class RefreshRequest(BaseModel):
    """A refresh-token rotation request."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """A refresh-token revocation request."""

    refresh_token: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_schemas.py tests/auth/test_security.py -v`
Expected: PASS (all tests in both files)

- [ ] **Step 5: Commit**

```bash
git add app/auth/schemas.py tests/auth/test_schemas.py
git commit -m "feat: add auth request/response schemas (ERP-026)"
```

---

### Task 6: Auth repository

**Files:**
- Create: `app/auth/repository.py`
- Test: `tests/auth/test_repository.py`

**Interfaces:**
- Consumes: `UserRecord`, `RefreshTokenRecord` (Task 3).
- Produces: `get_user_by_email(session, email) -> UserRecord | None`, `create_user(session, email, hashed_password, role="user") -> UserRecord`, `create_refresh_token(session, user_id, token_hash, expires_at) -> RefreshTokenRecord`, `get_refresh_token_by_hash(session, token_hash) -> RefreshTokenRecord | None`, `revoke_refresh_token(session, record) -> None`.

- [ ] **Step 1: Write the failing test — `tests/auth/test_repository.py`**

```python
import uuid
from datetime import datetime, timedelta, timezone

from app.auth.repository import (
    create_refresh_token,
    create_user,
    get_refresh_token_by_hash,
    get_user_by_email,
    revoke_refresh_token,
)
from app.core.db import get_session_factory


def test_create_user_and_get_by_email_round_trip():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, "repo-test@example.com", "hashed", role="admin")
        session.commit()

        found = get_user_by_email(session, "repo-test@example.com")
        assert found is not None
        assert found.id == user.id
        assert found.role == "admin"


def test_get_user_by_email_returns_none_for_unknown_email():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_user_by_email(session, "no-such-user@example.com") is None


def test_refresh_token_lifecycle():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, "refresh-test@example.com", "hashed")
        session.flush()
        expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        record = create_refresh_token(session, user.id, "a-token-hash", expires_at)
        session.commit()

        found = get_refresh_token_by_hash(session, "a-token-hash")
        assert found is not None
        assert found.id == record.id
        assert found.revoked_at is None

        revoke_refresh_token(session, found)
        session.commit()

        revoked = get_refresh_token_by_hash(session, "a-token-hash")
        assert revoked is not None
        assert revoked.revoked_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.repository'`

- [ ] **Step 3: Write `app/auth/repository.py`**

```python
"""Persistence for users and refresh tokens."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import RefreshTokenRecord, UserRecord


def get_user_by_email(session: Session, email: str) -> UserRecord | None:
    """Return the user with `email`, or `None` if none exists."""
    return session.scalars(select(UserRecord).where(UserRecord.email == email)).first()


def create_user(
    session: Session, email: str, hashed_password: str, role: str = "user"
) -> UserRecord:
    """Create and flush a new user row. Does not commit — the caller controls the transaction."""
    user = UserRecord(email=email, hashed_password=hashed_password, role=role)
    session.add(user)
    session.flush()
    return user


def create_refresh_token(
    session: Session, user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> RefreshTokenRecord:
    """Create and flush a new refresh-token row. Does not commit."""
    record = RefreshTokenRecord(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    session.add(record)
    session.flush()
    return record


def get_refresh_token_by_hash(session: Session, token_hash: str) -> RefreshTokenRecord | None:
    """Return the refresh-token row for `token_hash`, or `None` if none exists."""
    return session.scalars(
        select(RefreshTokenRecord).where(RefreshTokenRecord.token_hash == token_hash)
    ).first()


def revoke_refresh_token(session: Session, record: RefreshTokenRecord) -> None:
    """Mark `record` revoked (idempotent). Does not commit."""
    record.revoked_at = datetime.now(timezone.utc)
    session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_repository.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/auth/repository.py tests/auth/test_repository.py
git commit -m "feat: add auth repository (ERP-026)"
```

---

### Task 7: Refresh-token revocation cache

**Files:**
- Create: `app/auth/cache.py`
- Test: `tests/auth/test_cache.py`

**Interfaces:**
- Consumes: `AuthSettings` (Task 2).
- Produces: `RevocationCache` protocol (`is_revoked(token_hash) -> bool`, `mark_revoked(token_hash, ttl_seconds) -> None`), `RedisRevocationCache`, `get_default_revocation_cache() -> RevocationCache`.

- [ ] **Step 1: Write the failing test — `tests/auth/test_cache.py`**

```python
from app.auth.cache import RedisRevocationCache


def test_mark_revoked_then_is_revoked_returns_true(auth_settings):
    cache = RedisRevocationCache(auth_settings)
    assert cache.is_revoked("some-hash") is False

    cache.mark_revoked("some-hash", ttl_seconds=60)

    assert cache.is_revoked("some-hash") is True


def test_mark_revoked_with_zero_ttl_is_a_no_op(auth_settings):
    cache = RedisRevocationCache(auth_settings)
    cache.mark_revoked("some-hash", ttl_seconds=0)
    assert cache.is_revoked("some-hash") is False


def test_is_revoked_degrades_to_false_on_redis_error(auth_settings, monkeypatch):
    import redis

    cache = RedisRevocationCache(auth_settings)

    def _raise(*args, **kwargs):
        raise redis.RedisError("connection refused")

    monkeypatch.setattr(cache._client, "exists", _raise)
    assert cache.is_revoked("some-hash") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.cache'`

- [ ] **Step 3: Write `app/auth/cache.py`**

```python
"""Cache-aside storage for revoked refresh tokens, backed by Redis.

Never load-bearing (ADR-003): a Redis outage degrades `is_revoked` to `False`, so
`POST /auth/refresh` always falls back to the authoritative Postgres revocation check —
this cache only ever provides a fast-path short-circuit for the "known revoked" case.
"""

import logging
from functools import lru_cache
from typing import Protocol

import redis

from app.auth.config import AuthSettings, get_auth_settings

logger = logging.getLogger(__name__)


class RevocationCache(Protocol):
    """Anything that can cache-aside "is this refresh token hash revoked?"."""

    def is_revoked(self, token_hash: str) -> bool:
        """Return `True` if `token_hash` is cached as revoked. `False` on a cache miss too."""
        ...

    def mark_revoked(self, token_hash: str, ttl_seconds: int) -> None:
        """Cache `token_hash` as revoked for `ttl_seconds`. No-op if `ttl_seconds <= 0`."""
        ...


class RedisRevocationCache:
    """`RevocationCache` backed by Redis."""

    def __init__(self, settings: AuthSettings) -> None:
        """Build a cache bound to `settings.redis_url`."""
        self._client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )

    @staticmethod
    def _key(token_hash: str) -> str:
        return f"revoked_refresh_token:{token_hash}"

    def is_revoked(self, token_hash: str) -> bool:
        """Return `True` if `token_hash` is cached as revoked, else `False` (miss or error)."""
        try:
            return bool(self._client.exists(self._key(token_hash)))
        except redis.RedisError:
            logger.exception("Redis GET failed while checking revocation; falling back to Postgres")
            return False

    def mark_revoked(self, token_hash: str, ttl_seconds: int) -> None:
        """Cache `token_hash` as revoked for `ttl_seconds`. No-op on Redis error or non-positive TTL."""
        if ttl_seconds <= 0:
            return
        try:
            self._client.set(self._key(token_hash), "1", ex=ttl_seconds)
        except redis.RedisError:
            logger.exception("Redis SET failed while marking token revoked; continuing")


@lru_cache
def get_default_revocation_cache() -> RevocationCache:
    """Return the process-wide cached default `RevocationCache` (a `RedisRevocationCache`)."""
    return RedisRevocationCache(get_auth_settings())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_cache.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/auth/cache.py tests/auth/test_cache.py
git commit -m "feat: add refresh-token revocation cache (ERP-026)"
```

---

### Task 8: Auth service

**Files:**
- Create: `app/auth/service.py`
- Test: `tests/auth/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7.
- Produces: `EmailAlreadyRegisteredError`, `InvalidCredentialsError`, `InvalidRefreshTokenError` (exceptions); `register_user(email, password) -> UserRecord`, `login(email, password, settings=None) -> TokenResponse`, `refresh_access_token(raw_refresh_token, settings=None, revocation_cache=None) -> TokenResponse`, `logout(raw_refresh_token, revocation_cache=None) -> None`. `register_user` takes no `settings` — registration never issues tokens, so it has no use for `AuthSettings`.

- [ ] **Step 1: Write the failing test — `tests/auth/test_service.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.service'`

- [ ] **Step 3: Write `app/auth/service.py`**

```python
"""Business logic for registration, login, refresh-token rotation, and logout."""

from datetime import datetime, timedelta, timezone

from app.auth.cache import RevocationCache, get_default_revocation_cache
from app.auth.config import AuthSettings, get_auth_settings
from app.auth.models import UserRecord
from app.auth.repository import (
    create_refresh_token,
    create_user,
    get_refresh_token_by_hash,
    get_user_by_email,
    revoke_refresh_token,
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
    """
    settings = settings or get_auth_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_email(session, email)
        if user is None or not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError
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
        if record is None or record.revoked_at is not None or record.expires_at < now:
            raise InvalidRefreshTokenError

        user = session.get(UserRecord, record.user_id)
        if user is None:
            raise InvalidRefreshTokenError

        remaining_ttl = max(0, int((record.expires_at - now).total_seconds()))
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
        remaining_ttl = max(0, int((record.expires_at - now).total_seconds()))
        revoke_refresh_token(session, record)
        session.commit()

    revocation_cache.mark_revoked(token_hash, remaining_ttl)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_service.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add app/auth/service.py tests/auth/test_service.py
git commit -m "feat: add auth service (register/login/refresh/logout) (ERP-026)"
```

---

### Task 9: Auth dependencies (`get_current_user`, `require_role`)

**Files:**
- Create: `app/auth/dependencies.py`
- Test: `tests/auth/test_dependencies.py`

**Interfaces:**
- Consumes: `decode_access_token`, `InvalidTokenError` (Task 4), `CurrentUser` (Task 4/5).
- Produces: `get_current_user(token: str = Depends(...)) -> CurrentUser` (raises `HTTPException(401)`), `require_role(role: str) -> Callable[..., CurrentUser]` (raises `HTTPException(403)`).

- [ ] **Step 1: Write the failing test — `tests/auth/test_dependencies.py`**

```python
import uuid

import pytest
from fastapi import HTTPException

from app.auth.dependencies import get_current_user, require_role
from app.auth.security import create_access_token


def test_get_current_user_accepts_valid_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)

    current_user = get_current_user(token)

    assert current_user.role == "user"


def test_get_current_user_rejects_invalid_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user("garbage")
    assert exc_info.value.status_code == 401


def test_require_role_allows_matching_role(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "admin", auth_settings)
    current_user = get_current_user(token)

    checker = require_role("admin")
    assert checker(current_user).role == "admin"


def test_require_role_rejects_wrong_role(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)
    current_user = get_current_user(token)

    checker = require_role("admin")
    with pytest.raises(HTTPException) as exc_info:
        checker(current_user)
    assert exc_info.value.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_dependencies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.dependencies'`

- [ ] **Step 3: Write `app/auth/dependencies.py`**

```python
"""FastAPI dependencies for extracting and enforcing the authenticated caller."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.config import get_auth_settings
from app.auth.schemas import CurrentUser
from app.auth.security import InvalidTokenError, decode_access_token

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(_oauth2_scheme)) -> CurrentUser:
    """Resolve and validate the caller's access token. Raises `HTTPException(401)` if invalid."""
    try:
        return decode_access_token(token, get_auth_settings())
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc


def require_role(role: str) -> Callable[[CurrentUser], CurrentUser]:
    """Build a dependency that additionally requires `current_user.role == role`.

    Raises `HTTPException(403)` if the caller's role doesn't match. Unused by any endpoint
    in this ticket (no admin-only endpoints ship yet — see the spec's deferred follow-ups)
    but provided so a future admin endpoint can depend on it directly.
    """

    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return current_user

    return _check
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/auth/test_dependencies.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/auth/dependencies.py tests/auth/test_dependencies.py
git commit -m "feat: add get_current_user and require_role dependencies (ERP-026)"
```

---

### Task 10: Auth router + register in `app/main.py`

**Files:**
- Create: `app/auth/router.py`
- Modify: `app/main.py`
- Test: `tests/auth/test_router.py`

**Interfaces:**
- Consumes: everything from Tasks 5–8.
- Produces: `router` (`APIRouter`, prefix `/auth`) with `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`.

- [ ] **Step 1: Write the failing test — `tests/auth/test_router.py`**

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_register_then_login_then_refresh_then_logout():
    register_response = client.post(
        "/auth/register",
        json={"email": "router-test@example.com", "password": "a-long-enough-password"},
    )
    assert register_response.status_code == 201
    assert register_response.json()["role"] == "user"

    login_response = client.post(
        "/auth/login",
        json={"email": "router-test@example.com", "password": "a-long-enough-password"},
    )
    assert login_response.status_code == 200
    tokens = login_response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    refresh_response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 200
    new_tokens = refresh_response.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    logout_response = client.post("/auth/logout", json={"refresh_token": new_tokens["refresh_token"]})
    assert logout_response.status_code == 204

    reuse_response = client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert reuse_response.status_code == 401


def test_register_rejects_duplicate_email():
    client.post(
        "/auth/register",
        json={"email": "dup-router-test@example.com", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/auth/register",
        json={"email": "dup-router-test@example.com", "password": "another-password"},
    )
    assert response.status_code == 409


def test_login_rejects_wrong_password():
    client.post(
        "/auth/register",
        json={"email": "wrongpw-router-test@example.com", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/auth/login",
        json={"email": "wrongpw-router-test@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


def test_refresh_rejects_unknown_token():
    response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/auth/test_router.py -v`
Expected: FAIL with `404` on `/auth/register` (router not yet registered) or `ModuleNotFoundError`

- [ ] **Step 3: Write `app/auth/router.py`**

```python
"""Auth API: registration, login, refresh-token rotation, and logout."""

from fastapi import APIRouter, HTTPException, status

from app.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.auth.service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.auth.service import login as login_user
from app.auth.service import logout as logout_user
from app.auth.service import refresh_access_token, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest) -> UserResponse:
    """Register a new user with the `user` role."""
    try:
        user = register_user(request.email, request.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return UserResponse(id=user.id, email=user.email, role=user.role)


@router.post("/login")
def login(request: LoginRequest) -> TokenResponse:
    """Exchange email + password for an access + refresh token pair."""
    try:
        return login_user(request.email, request.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from exc


@router.post("/refresh")
def refresh(request: RefreshRequest) -> TokenResponse:
    """Rotate a refresh token for a new access + refresh token pair."""
    try:
        return refresh_access_token(request.refresh_token)
    except InvalidRefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: LogoutRequest) -> None:
    """Revoke a refresh token."""
    logout_user(request.refresh_token)
```

- [ ] **Step 4: Register the router in `app/main.py`**

Modify `app/main.py`:
```python
"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.generation.router import conversations_router
from app.generation.router import router as generation_router
from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router

app = FastAPI(title="Enterprise RAG Platform")
app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(retrieval_router)
app.include_router(generation_router)
app.include_router(conversations_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/auth/ -v`
Expected: PASS (all auth tests so far)

- [ ] **Step 6: Commit**

```bash
git add app/auth/router.py app/main.py tests/auth/test_router.py
git commit -m "feat: add auth router, register at /auth (ERP-026)"
```

---

### Task 11: `owner_id` migration for `documents`/`conversations` + backfill

**Files:**
- Modify: `app/ingestion/models.py`
- Modify: `app/generation/models.py`
- Create: `alembic/versions/<new>_add_owner_id_to_documents_and_conversations.py`

**Interfaces:**
- Consumes: `UserRecord` (Task 3).
- Produces: `DocumentRecord.owner_id: Mapped[uuid.UUID]` (FK→`users.id`, not null), `ConversationRecord.owner_id: Mapped[uuid.UUID]` (FK→`users.id`, not null).

- [ ] **Step 1: Add `owner_id` to `DocumentRecord`**

Modify `app/ingestion/models.py` — add the import and column:
```python
import uuid
from datetime import datetime

from sqlalchemy import DDL, JSON, ForeignKey, Identity, Index, Text, event
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all ingestion ORM models."""


class DocumentRecord(Base):
    """A single ingested document."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(primary_key=True)
    filename: Mapped[str]
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

(The rest of `app/ingestion/models.py` — `ChunkRecord` and the trigger `event.listen` calls — is unchanged.)

- [ ] **Step 2: Add `owner_id` to `ConversationRecord`**

Modify `app/generation/models.py`:
```python
"""SQLAlchemy ORM models for multi-turn conversation memory (ERP-018)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Identity, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class ConversationRecord(Base):
    """A single multi-turn conversation. `id` is always client-supplied, never generated here."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

(`ConversationMessageRecord` below it is unchanged — ownership lives on `ConversationRecord` only.)

- [ ] **Step 3: Verify the models import cleanly**

Run: `uv run python -c "from app.ingestion.models import DocumentRecord; from app.generation.models import ConversationRecord; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Generate the migration**

Run: `uv run alembic revision -m "add owner_id to documents and conversations"` (plain, not `--autogenerate` — this migration needs the hand-written backfill step autogenerate can't produce).

Write the generated file's body:
```python
"""add owner_id to documents and conversations

Revision ID: <generated>
Revises: <previous head>
Create Date: <generated>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "<previous head>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"
_SYSTEM_USER_EMAIL = "system@internal"
_SYSTEM_USER_HASH = "!"  # not a valid Argon2 hash -- this account can never log in


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("conversations", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO users (id, email, hashed_password, role) "
            "VALUES (:id, :email, :hashed_password, 'admin') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": _SYSTEM_USER_ID, "email": _SYSTEM_USER_EMAIL, "hashed_password": _SYSTEM_USER_HASH},
    )
    connection.execute(
        sa.text("UPDATE documents SET owner_id = :id WHERE owner_id IS NULL"),
        {"id": _SYSTEM_USER_ID},
    )
    connection.execute(
        sa.text("UPDATE conversations SET owner_id = :id WHERE owner_id IS NULL"),
        {"id": _SYSTEM_USER_ID},
    )

    op.alter_column("documents", "owner_id", nullable=False)
    op.alter_column("conversations", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_owner_id_users", "documents", "users", ["owner_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_conversations_owner_id_users", "conversations", "users", ["owner_id"], ["id"]
    )
    op.create_index(op.f("ix_documents_owner_id"), "documents", ["owner_id"])
    op.create_index(op.f("ix_conversations_owner_id"), "conversations", ["owner_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_conversations_owner_id"), table_name="conversations")
    op.drop_index(op.f("ix_documents_owner_id"), table_name="documents")
    op.drop_constraint("fk_conversations_owner_id_users", "conversations", type_="foreignkey")
    op.drop_constraint("fk_documents_owner_id_users", "documents", type_="foreignkey")
    op.drop_column("conversations", "owner_id")
    op.drop_column("documents", "owner_id")
```

Note: this migration must be chained after Task 3's `create users and refresh tokens tables` migration (`down_revision` set accordingly) — `users` must exist before this migration's `INSERT INTO users` runs. Confirm the head with `uv run alembic heads` before writing `down_revision`.

- [ ] **Step 5: Verify the migration runs cleanly against a real Postgres**

Run:
```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```
Expected: all three succeed with no errors.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/models.py app/generation/models.py alembic/versions/
git commit -m "feat: add owner_id to documents and conversations, backfill system user (ERP-026)"
```

---

### Task 12: Thread `owner_id` through ingestion

**Files:**
- Modify: `app/ingestion/repository.py:12-19` (`save_document_and_chunks`)
- Modify: `app/embedding/service.py:11-18` (`embed_and_persist`)
- Modify: `app/ingestion/jobs.py` (`JobRecord`, `create_job`, `run_ingestion_job`)
- Modify: `app/ingestion/router.py`
- Modify: `tests/ingestion/test_repository.py`, `tests/embedding/test_service.py` (check for `embed_and_persist` calls), `tests/ingestion/test_jobs.py`, `tests/ingestion/test_router.py`

**Interfaces:**
- Consumes: `CurrentUser`, `get_current_user` (Task 9).
- Produces: `save_document_and_chunks(session, document_id, source_filename, chunks, owner_id)`, `embed_and_persist(..., owner_id)`, `jobs.create_job(owner_id) -> str`, `JobRecord.owner_id: uuid.UUID`.

- [ ] **Step 1: Add `owner_id` to `save_document_and_chunks`**

Modify `app/ingestion/repository.py:12-19`:
```python
def save_document_and_chunks(
    session: Session,
    document_id: str,
    source_filename: str,
    chunks: list[Chunk],
    owner_id: uuid.UUID,
) -> list[ChunkRecord]:
    """Persist one document and its chunks in `session`, flushing so `vector_id`s are assigned.

    Does not commit — the caller controls the transaction boundary.
    """
    session.add(DocumentRecord(document_id=document_id, filename=source_filename, owner_id=owner_id))
    session.flush()
```

Add `import uuid` at the top of `app/ingestion/repository.py`.

- [ ] **Step 2: Add `owner_id` to `embed_and_persist`**

Modify `app/embedding/service.py`:
```python
"""Orchestrates embedding a document's chunks and persisting them to Postgres + FAISS."""

import uuid

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import FaissIndex
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk


def embed_and_persist(
    document_id: str,
    source_filename: str,
    chunks: list[Chunk],
    owner_id: uuid.UUID,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> None:
    """Embed `chunks`, persist them to Postgres, and add their vectors to the FAISS index.

    No-op if `chunks` is empty. `embedding_client`/`faiss_index` are injectable for testing;
    default to Ollama/local-disk implementations built from `settings` (or the process-wide
    cached `EmbeddingSettings` if `settings` is not given).
    """
    if not chunks:
        return

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    vectors = embedding_client.embed([chunk.text for chunk in chunks])

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, source_filename, chunks, owner_id)
        vector_ids = [record.vector_id for record in records]
        session.commit()

    faiss_index.add(vector_ids, vectors)
    faiss_index.save()
```

- [ ] **Step 3: Thread `owner_id` through job tracking**

Modify `app/ingestion/jobs.py`:
```python
"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import logging
import threading
import uuid

from app.embedding.client import EmbeddingClient
from app.embedding.index import FaissIndex
from app.embedding.service import embed_and_persist
from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

logger = logging.getLogger(__name__)

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()


class JobRecord:
    """Mutable state for one tracked ingestion job."""

    def __init__(self, owner_id: uuid.UUID) -> None:
        """Initialize a new job in PENDING status, owned by `owner_id`, with no result or error yet."""
        self.owner_id = owner_id
        self.status: JobStatus = JobStatus.PENDING
        self.result: IngestResponse | None = None
        self.error: str | None = None


def create_job(owner_id: uuid.UUID) -> str:
    """Register a new PENDING job owned by `owner_id` and return its ID."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobRecord(owner_id)
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    """Look up a job by ID, or None if it doesn't exist."""
    with _lock:
        return _jobs.get(job_id)


def run_ingestion_job(
    job_id: str,
    pdf_path: str,
    filename: str,
    settings: IngestionSettings,
    owner_id: uuid.UUID,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record.

    On success, also embeds and durably persists the resulting chunks (Postgres + FAISS),
    stamping `owner_id` as the resulting document's owner — a DONE job means the data is
    embedded and persisted, not just held in memory.
    """
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
        embed_and_persist(
            document_id=result.document_id,
            source_filename=filename,
            chunks=result.chunks,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index=faiss_index,
        )
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        logger.exception("Ingestion job %s failed for file %r", job_id, filename)
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
```

- [ ] **Step 4: Protect and wire the ingestion router**

Modify `app/ingestion/router.py`:
```python
"""Ingestion API: PDF upload (async job) and job-status polling."""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.ingestion import jobs
from app.ingestion.config import get_settings
from app.ingestion.schemas import JobStatusResponse

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_PDF_MAGIC = b"%PDF-"
_COPY_CHUNK_SIZE = 1024 * 1024


@router.post("/pdf", status_code=status.HTTP_202_ACCEPTED)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    """Validate and stream an uploaded PDF to disk, then schedule an async ingestion job."""
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF (content-type application/pdf)")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    header = await file.read(5)
    if header != _PDF_MAGIC:
        raise HTTPException(status_code=400, detail="File is not a valid PDF (missing %PDF- header)")
    await file.seek(0)

    settings = get_settings()
    max_size = settings.max_upload_size_bytes

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / file.filename
    total_bytes = 0
    try:
        with tmp_path.open("wb") as f:
            while chunk := await file.read(_COPY_CHUNK_SIZE):
                total_bytes += len(chunk)
                if total_bytes > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"File exceeds maximum upload size of {max_size} bytes",
                    )
                f.write(chunk)
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    job_id = jobs.create_job(current_user.id)
    background_tasks.add_task(
        jobs.run_ingestion_job, job_id, str(tmp_path), file.filename, settings, current_user.id
    )

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: str, current_user: CurrentUser = Depends(get_current_user)
) -> JobStatusResponse:
    """Return the current status (and result or error, once finished) of an ingestion job.

    Returns 404 (not just for an unknown ID, but also for a job owned by a different user)
    so a caller can't distinguish "doesn't exist" from "exists but isn't yours".
    """
    record = jobs.get_job(job_id)
    if record is None or record.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(status=record.status, result=record.result, error=record.error)
```

- [ ] **Step 5: Update existing tests' call sites**

Modify `tests/ingestion/test_repository.py`: every `save_document_and_chunks(session, document_id, "doc.pdf", chunks)` call needs a trailing `owner_id` argument. Add near the top of the file, after the imports:
```python
import uuid

from app.auth.repository import create_user

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()
```
Then, in every test function in this file, call `_ensure_test_owner(session)` right after opening the session (before the first `save_document_and_chunks` call), and add `, _TEST_OWNER_ID` as the final positional argument to every `save_document_and_chunks(...)` call in the file.

Apply the identical pattern (`_ensure_test_owner` + trailing `_TEST_OWNER_ID` arg) to every `save_document_and_chunks(...)` call site in `tests/retrieval/test_service.py` (via its own `_persist_and_index` helper at line 54-60 — add the `owner_id` parameter to `_persist_and_index` itself and thread it from each call site) and in `tests/embedding/test_service.py` wherever `embed_and_persist(...)` is called directly (add `owner_id=_TEST_OWNER_ID` as a keyword argument there instead, since `embed_and_persist` takes it as a named param).

Modify `tests/ingestion/test_jobs.py`: every `jobs.create_job()` call becomes `jobs.create_job(_TEST_OWNER_ID)`, and every `jobs.run_ingestion_job(job_id, pdf_path, filename, settings, ...)` call gains `current_user.id`-equivalent — insert `_TEST_OWNER_ID` as the 5th positional argument (immediately after `settings`, before any injected `embedding_client`/`faiss_index`).

Modify `tests/ingestion/test_router.py`: add an `auth_headers` fixture (session-scoped is wrong here since each test needs a fresh user only once per file — use a module-level fixture) at the top of the file:
```python
@pytest.fixture
def auth_headers():
    email = f"ingestion-test-{uuid.uuid4()}@example.com"
    client.post("/auth/register", json={"email": email, "password": "a-long-enough-password"})
    login_response = client.post("/auth/login", json={"email": email, "password": "a-long-enough-password"})
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
```
(add `import uuid` at the top if not already present). Then add `headers=auth_headers` as a keyword argument to every `client.post("/ingestion/pdf", ...)` and `client.get(f"/ingestion/jobs/{job_id}")` call in the file, and add `auth_headers` as a parameter to every test function that makes such a call (pytest fixture injection).

- [ ] **Step 6: Run the full ingestion + embedding + retrieval test suites**

Run: `uv run pytest tests/ingestion tests/embedding tests/retrieval -v`
Expected: PASS (all tests updated in Step 5 pass; any remaining failures point at a missed call site from Step 5 — fix and re-run)

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/repository.py app/embedding/service.py app/ingestion/jobs.py app/ingestion/router.py tests/ingestion tests/embedding tests/retrieval/test_service.py
git commit -m "feat: enforce per-user ownership on ingestion (ERP-026)"
```

---

### Task 13: Per-user isolation in retrieval

**Files:**
- Modify: `app/ingestion/repository.py` (`get_chunks_by_vector_ids`, `search_chunks_by_text`)
- Modify: `app/retrieval/service.py` (`search`, `_cache_key`)
- Modify: `app/retrieval/router.py`
- Modify: `tests/retrieval/test_service.py`, `tests/retrieval/test_router.py`, `tests/ingestion/test_repository.py`

**Interfaces:**
- Consumes: `CurrentUser`, `get_current_user` (Task 9).
- Produces: `search(query, top_k, owner_id, ...) -> list[RetrievedChunk]` (new required `owner_id` param), `get_chunks_by_vector_ids(session, vector_ids, owner_id)`, `search_chunks_by_text(session, query_text, k, owner_id)`.

- [ ] **Step 1: Filter chunk lookups by `owner_id` in the repository**

Modify `app/ingestion/repository.py` — both functions join through `ChunkRecord.document_id` to `DocumentRecord.owner_id`:
```python
def get_chunks_by_vector_ids(
    session: Session, vector_ids: list[int], owner_id: uuid.UUID
) -> dict[int, ChunkRecord]:
    """Fetch chunk rows by their `vector_id`s, restricted to `owner_id`'s documents.

    Keyed by `vector_id`. `{}` for empty input.
    """
    if not vector_ids:
        return {}
    rows = session.scalars(
        select(ChunkRecord)
        .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
        .where(ChunkRecord.vector_id.in_(vector_ids), DocumentRecord.owner_id == owner_id)
    ).all()
    return {row.vector_id: row for row in rows}


def search_chunks_by_text(
    session: Session, query_text: str, k: int, owner_id: uuid.UUID
) -> list[tuple[int, float]]:
    """Full-text search chunk text via Postgres, restricted to `owner_id`'s documents.

    Returns `(vector_id, rank)` pairs, best-first. `[]` for a blank query, `k <= 0`, or no
    matching chunks. Uses `plainto_tsquery` (safe against arbitrary user input, no `tsquery`
    syntax to escape) against the generated `search_vector` column, ranked by `ts_rank`.
    """
    if not query_text.strip() or k <= 0:
        return []
    tsquery = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank(ChunkRecord.search_vector, tsquery).label("rank")
    rows = session.execute(
        select(ChunkRecord.vector_id, rank)
        .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
        .where(ChunkRecord.search_vector.op("@@")(tsquery), DocumentRecord.owner_id == owner_id)
        .order_by(rank.desc())
        .limit(k)
    ).all()
    return [(int(vector_id), float(rank_value)) for vector_id, rank_value in rows]
```

- [ ] **Step 2: Thread `owner_id` through `search()` and the cache key**

Modify `app/retrieval/service.py`:
```python
def _cache_key(query: str, top_k: int, rerank: bool, expand_sections: bool, owner_id: uuid.UUID) -> str:
    """Hash the parameters that determine `search()`'s output, for cache lookups.

    `owner_id` is part of the key -- without it, one user's cached results could leak to
    another user issuing the same query text.
    """
    query_bytes = query.encode()
    owner_bytes = str(owner_id).encode()
    payload = (
        len(query_bytes).to_bytes(4, "big")
        + query_bytes
        + top_k.to_bytes(4, "big")
        + bytes([rerank, expand_sections])
        + owner_bytes
    )
    return hashlib.sha256(payload).hexdigest()
```

Add `import uuid` near the top of `app/retrieval/service.py`. Update `search`'s signature and body:
```python
def search(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index: FaissIndex | None = None,
    rerank: bool = False,
    reranker: Reranker | None = None,
    expand_sections: bool = False,
    cache: RetrievalCache | None = None,
) -> list[RetrievedChunk]:
    """Run hybrid (vector + BM25) search restricted to `owner_id`'s documents.

    Returns up to `top_k` chunks, fused-score order. `embedding_client`/`faiss_index`/
    `settings` are injectable for testing; default to Ollama/local-disk implementations
    built from `settings` (or the process-wide cached `EmbeddingSettings` if `settings` is
    not given).

    If `rerank` is true, the fused+hydrated results are re-scored and reordered by
    `reranker` (a `FlashRankReranker` built from the process-wide `RerankerSettings` if none
    is injected) before being returned. If `rerank` is false (the default), `reranker` is
    never constructed or invoked, so opting out costs nothing.

    If `expand_sections` is true, the (possibly reranked) results are expanded with each
    result's section-siblings (see `_expand_sections`) -- the returned list may then be
    longer than `top_k`; this is intended, not a bug.

    `cache` is an injectable `RetrievalCache` (defaulting to `RedisRetrievalCache`); the
    full result of this function, keyed by (`query`, `top_k`, `rerank`, `expand_sections`,
    `owner_id`), is cache-aside -- a hit returns immediately without running any of the
    pipeline below.

    Isolation is enforced via oversample-then-filter: FAISS and BM25 candidates are still
    drawn from the full shared index/table (not per-user partitioned), then filtered to
    `owner_id`'s documents during Postgres hydration. This is correct at today's scale but
    can degrade recall once a single user's chunks are a small fraction of a large shared
    index -- see the design spec's Future Follow-ups for the deferred partitioned-index
    alternative.
    """
    cache = cache or get_default_retrieval_cache()
    cache_key = _cache_key(query, top_k, rerank, expand_sections, owner_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index = faiss_index or FaissIndex(settings.faiss_index_path, settings.dimension)

    candidate_k = top_k * RRF_OVERSAMPLE_MULTIPLIER

    vectors = embedding_client.embed([query])
    if not vectors:
        raise ValueError("embedding client returned no vectors for the query")
    vector_hits = faiss_index.search(vectors[0], candidate_k)
    vector_ranked_ids = [vector_id for vector_id, _ in vector_hits]

    session_factory = get_session_factory()
    with session_factory() as session:
        bm25_hits = search_chunks_by_text(session, query, candidate_k, owner_id)
        bm25_ranked_ids = [vector_id for vector_id, _ in bm25_hits]

        fused = _reciprocal_rank_fusion(vector_ranked_ids, bm25_ranked_ids)[:top_k]
        if not fused:
            cache.set(cache_key, [])
            return []

        chunks_by_vector_id = get_chunks_by_vector_ids(
            session, [vector_id for vector_id, _ in fused], owner_id
        )
        results = []
        for vector_id, score in fused:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
                logger.warning("Dropping fused hit with no matching chunk row: vector_id=%s", vector_id)
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_filename=chunk.source_filename,
                    score=score,
                )
            )

        if rerank:
            reranker = reranker or FlashRankReranker(get_reranker_settings())
            results = reranker.rerank(query, results)

        if expand_sections:
            results = _expand_sections(session, results)

        cache.set(cache_key, results)
        return results
```

Note: `_expand_sections`'s call to `get_sibling_chunks` (unchanged in this task) does not filter by owner — since its siblings are drawn from the *same document* as an already-owner-filtered anchor chunk, no cross-user leak is possible (a document has exactly one owner).

- [ ] **Step 3: Protect and wire the retrieval router**

Modify `app/retrieval/router.py`:
```python
"""Retrieval API: semantic search over ingested chunks."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.retrieval.schemas import RetrievalQuery, RetrievalResponse
from app.retrieval.service import search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/query")
def query(
    query_request: RetrievalQuery, current_user: CurrentUser = Depends(get_current_user)
) -> RetrievalResponse:
    """Run a semantic search query, restricted to the caller's own documents."""
    try:
        results = search(
            query_request.query,
            query_request.top_k,
            current_user.id,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
        )
    except Exception as exc:
        logger.exception("Retrieval query failed")
        raise HTTPException(status_code=503, detail="Retrieval query failed") from exc
    return RetrievalResponse(results=results)
```

- [ ] **Step 4: Update existing tests' call sites**

Modify `tests/retrieval/test_service.py`: add `_TEST_OWNER_ID = uuid.uuid4()` near the top (alongside a helper that inserts that user row via `create_user`, same pattern as Task 12 Step 5's `_ensure_test_owner`); pass it as `search(...)`'s 3rd positional argument (after `query`, `top_k`) at every call site in the file, and as `_persist_and_index`'s new `owner_id` parameter (threaded to its `save_document_and_chunks` call, per Task 12 Step 5).

Modify `tests/retrieval/test_router.py`: add the same `auth_headers` fixture pattern as Task 12 Step 5 (register + login a fresh user, return `{"Authorization": ...}` headers), and add `headers=auth_headers` to every `client.post("/retrieval/query", ...)` call plus the ingestion upload/poll calls in `test_query_returns_ingested_chunk` (which must use the *same* `auth_headers` for both the upload and the query, so the ingested document and the query are the same owner).

Modify `tests/ingestion/test_repository.py`: `get_chunks_by_vector_ids` calls gain a trailing `_TEST_OWNER_ID` argument (reuse the fixture from Task 12 Step 5); `search_chunks_by_text` calls likewise.

- [ ] **Step 5: Add a cross-owner isolation test**

Add to `tests/retrieval/test_router.py`:
```python
def test_query_does_not_return_another_users_document(simple_text_pdf):
    owner_a_headers = _register_and_login("isolation-a")
    owner_b_headers = _register_and_login("isolation-b")

    with open(simple_text_pdf, "rb") as pdf_file:
        upload = client.post(
            "/ingestion/pdf",
            files={"file": ("simple.pdf", pdf_file, "application/pdf")},
            headers=owner_a_headers,
        )
    job_id = upload.json()["job_id"]

    deadline = time.monotonic() + 60.0
    status_body = None
    while time.monotonic() < deadline:
        status_response = client.get(f"/ingestion/jobs/{job_id}", headers=owner_a_headers)
        status_body = status_response.json()
        if status_body["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status_body["status"] == "done"

    response = client.post(
        "/retrieval/query",
        json={"query": "introduction", "top_k": 3},
        headers=owner_b_headers,
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
```
Refactor the `auth_headers` fixture into a shared helper `_register_and_login(prefix: str) -> dict[str, str]` (a plain function, not a fixture, so it can be called twice with different prefixes in one test) that both the fixture and this new test use.

- [ ] **Step 6: Run the retrieval and ingestion test suites**

Run: `uv run pytest tests/retrieval tests/ingestion -v`
Expected: PASS (all tests, including the new isolation test)

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/repository.py app/retrieval/service.py app/retrieval/router.py tests/retrieval tests/ingestion/test_repository.py
git commit -m "feat: enforce per-user isolation in retrieval (ERP-026)"
```

---

### Task 14: Per-user isolation in generation + conversations

**Files:**
- Modify: `app/generation/models.py` (already has `owner_id` from Task 11 — no change here)
- Modify: `app/generation/repository.py` (`get_or_create_conversation`, `get_conversation`)
- Modify: `app/generation/service.py` (`generate`, `generate_stream`, `get_conversation_history`)
- Modify: `app/generation/router.py`
- Modify: `tests/generation/test_repository.py`, `tests/generation/test_service.py`, `tests/generation/test_router.py`

**Interfaces:**
- Consumes: `CurrentUser`, `get_current_user` (Task 9); `search(..., owner_id, ...)` (Task 13).
- Produces: `get_or_create_conversation(session, conversation_id, owner_id)`, `get_conversation(session, conversation_id, owner_id)`, `generate(..., owner_id)`, `generate_stream(..., owner_id)`, `get_conversation_history(conversation_id, owner_id)`.

- [ ] **Step 1: Enforce ownership in the conversation repository**

Modify `app/generation/repository.py`:
```python
"""Persistence for multi-turn conversations."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.models import ConversationMessageRecord, ConversationRecord


def get_or_create_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> ConversationRecord:
    """Return `conversation_id`'s `ConversationRecord`, creating it (owned by `owner_id`) if new.

    Does not commit -- the caller controls the transaction boundary.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    if conversation is None:
        conversation = ConversationRecord(id=conversation_id, owner_id=owner_id)
        session.add(conversation)
        session.flush()
    return conversation


def append_message(
    session: Session, conversation_id: uuid.UUID, role: str, content: str
) -> ConversationMessageRecord:
    """Append one message to `conversation_id`. Does not commit."""
    message = ConversationMessageRecord(
        id=uuid.uuid4(), conversation_id=conversation_id, role=role, content=content
    )
    session.add(message)
    session.flush()
    return message


def get_recent_messages(
    session: Session, conversation_id: uuid.UUID, limit: int
) -> list[ConversationMessageRecord]:
    """Return up to `limit` most recent messages for `conversation_id`, oldest first.

    `[]` if the conversation doesn't exist or has no messages yet.
    """
    rows = session.scalars(
        select(ConversationMessageRecord)
        .where(ConversationMessageRecord.conversation_id == conversation_id)
        .order_by(ConversationMessageRecord.sequence.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def get_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> ConversationRecord | None:
    """Return `conversation_id`'s `ConversationRecord` if it exists and belongs to `owner_id`.

    Returns `None` both when the conversation doesn't exist and when it belongs to a
    different owner -- callers can't distinguish the two, matching the ingestion job
    404 convention. Unlike `get_or_create_conversation`, never creates a row.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    if conversation is None or conversation.owner_id != owner_id:
        return None
    return conversation


def get_all_messages(session: Session, conversation_id: uuid.UUID) -> list[ConversationMessageRecord]:
    """Return every message for `conversation_id`, oldest first. `[]` if none exist."""
    return list(
        session.scalars(
            select(ConversationMessageRecord)
            .where(ConversationMessageRecord.conversation_id == conversation_id)
            .order_by(ConversationMessageRecord.sequence.asc())
        ).all()
    )
```

- [ ] **Step 2: Thread `owner_id` through the generation service**

Modify `app/generation/service.py`: add `owner_id: uuid.UUID` as a required parameter (after `top_k`, matching `search()`'s parameter order from Task 13) to `generate`, `generate_stream`, and `get_conversation_history`. Every internal `retrieval_search(query, top_k, rerank=..., expand_sections=...)` call gains `owner_id` as the 3rd positional argument; every `get_or_create_conversation(session, conversation_id)` call becomes `get_or_create_conversation(session, conversation_id, owner_id)`; every `get_conversation(session, conversation_id)` call becomes `get_conversation(session, conversation_id, owner_id)`.

The full rewritten `app/generation/service.py`:
```python
"""Grounded answer generation over hybrid-retrieved chunks, with optional multi-turn memory."""

import logging
import uuid
from typing import Any, Iterator

from app.core.db import get_session_factory
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.repository import (
    append_message,
    get_all_messages,
    get_conversation,
    get_or_create_conversation,
    get_recent_messages,
)
from app.generation.rewrite import rewrite_query
from app.generation.schemas import (
    Citation,
    ConversationHistoryResponse,
    ConversationTurn,
    GenerationResponse,
    Message,
)
from app.retrieval.schemas import RetrievedChunk
from app.retrieval.service import search as retrieval_search

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = "I don't have enough information in the ingested documents to answer this question."


def _citations_for(chunks: list[RetrievedChunk]) -> list[Citation]:
    return [
        Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_filename=chunk.source_filename,
        )
        for chunk in chunks
    ]


def generate(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResponse:
    """Retrieve context for `query` (restricted to `owner_id`'s documents) and synthesize a
    grounded, citation-marked answer.

    `conversation_id` is a stateless/stateful switch. `None` (the default) is fully
    stateless: no session opened, no history loaded, no rewriting, nothing persisted --
    behavior is identical to the single-turn-only version of this function. Given a
    `conversation_id`, the last `settings.history_window_turns` messages are loaded (empty
    on a conversation's first turn) via a short-lived read session that is closed before
    rewriting/retrieval/generation run; no DB session is held open across those LLM calls.
    If history exists, `query` is rewritten into a standalone retrieval query via
    `rewrite_query` before running retrieval, and that history is rendered into the
    generation prompt. Both the raw user turn and the assistant's answer are persisted
    together in one transaction via a second, separately opened write session, but only
    after generation succeeds -- a failure commits nothing (the write session isn't even
    opened until `answer`/`citations` are fully computed). A conversation created here is
    owned by `owner_id`; an existing conversation belonging to a different owner is treated
    as brand new (see `get_or_create_conversation`) rather than raising, since `generate`
    has no read-then-reject path -- ownership enforcement for reads happens in
    `get_conversation_history`.

    Runs the existing hybrid retrieval pipeline unmodified (`rerank`/`expand_sections`
    passed straight through). If retrieval returns no chunks, short-circuits to
    `NO_CONTEXT_ANSWER` without constructing or calling an `LLMClient` for the final answer
    (a conversational short-circuit still persists both turns, so the conversation record
    reflects that the question went unanswered).

    `settings`/`llm_client` are injectable for testing; default to the process-wide
    cached `GenerationSettings` and an `OllamaLLMClient` built from it.
    """
    settings = settings or get_generation_settings()

    if conversation_id is None:
        chunks = retrieval_search(query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections)
        if not chunks:
            return GenerationResponse(answer=NO_CONTEXT_ANSWER, citations=[], conversation_id=None)

        llm_client = llm_client or OllamaLLMClient(settings)
        user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        return GenerationResponse(
            answer=answer, citations=_citations_for(included_chunks), conversation_id=None
        )

    session_factory = get_session_factory()

    with session_factory() as read_session:
        history_records = get_recent_messages(
            read_session, conversation_id, settings.history_window_turns
        )
        history = [ConversationTurn(role=r.role, content=r.content) for r in history_records]

    if history:
        llm_client = llm_client or OllamaLLMClient(settings)
        rewritten_query = rewrite_query(query, history, llm_client)
    else:
        rewritten_query = query

    chunks = retrieval_search(
        rewritten_query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections
    )
    if not chunks:
        answer = NO_CONTEXT_ANSWER
        citations: list[Citation] = []
    else:
        llm_client = llm_client or OllamaLLMClient(settings)
        user_prompt, included_chunks = build_prompt(
            query, chunks, settings.max_context_chars, history=history
        )
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        citations = _citations_for(included_chunks)

    with session_factory() as write_session:
        get_or_create_conversation(write_session, conversation_id, owner_id)
        append_message(write_session, conversation_id, "user", query)
        append_message(write_session, conversation_id, "assistant", answer)
        write_session.commit()

    return GenerationResponse(answer=answer, citations=citations, conversation_id=conversation_id)


def generate_stream(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Streaming counterpart to `generate`: yields `(event, data)` tuples instead of returning one response.

    Event sequence on success: one `("citations", {"citations": [...]})`, zero or more
    `("token", {"text": "..."})` (one per chunk of generated text), then a terminal
    `("done", {"conversation_id": str | None})`. On any failure, yields a terminal
    `("error", {"detail": "..."})` instead of `"done"` -- callers must treat `"error"` as
    the end of the stream, not attempt to resume iteration.

    Shares `generate()`'s stateless/stateful branching, rewrite, ownership, and persistence
    semantics exactly (see `generate`'s docstring) -- only the delivery mechanism differs.
    Persistence for a stateful request happens only after the full answer is assembled,
    immediately before the `"done"` event, so a client disconnect (which raises
    `GeneratorExit` at the suspended `yield`) or a mid-generation exception both skip it,
    leaving conversation history exactly as it was before the request.
    """
    try:
        settings = settings or get_generation_settings()
        if conversation_id is None:
            chunks = retrieval_search(
                query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections
            )
            if not chunks:
                yield "citations", {"citations": []}
                yield "token", {"text": NO_CONTEXT_ANSWER}
                yield "done", {"conversation_id": None}
                return

            llm_client = llm_client or OllamaLLMClient(settings)
            user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
            yield "citations", {"citations": [c.model_dump() for c in _citations_for(included_chunks)]}
            for piece in llm_client.generate_stream(SYSTEM_PROMPT, user_prompt):
                yield "token", {"text": piece}
            yield "done", {"conversation_id": None}
            return

        session_factory = get_session_factory()
        with session_factory() as read_session:
            history_records = get_recent_messages(
                read_session, conversation_id, settings.history_window_turns
            )
            history = [ConversationTurn(role=r.role, content=r.content) for r in history_records]

        if history:
            llm_client = llm_client or OllamaLLMClient(settings)
            rewritten_query = rewrite_query(query, history, llm_client)
        else:
            rewritten_query = query

        chunks = retrieval_search(
            rewritten_query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections
        )
        if not chunks:
            yield "citations", {"citations": []}
            yield "token", {"text": NO_CONTEXT_ANSWER}
            answer = NO_CONTEXT_ANSWER
        else:
            llm_client = llm_client or OllamaLLMClient(settings)
            user_prompt, included_chunks = build_prompt(
                query, chunks, settings.max_context_chars, history=history
            )
            yield "citations", {"citations": [c.model_dump() for c in _citations_for(included_chunks)]}
            answer_parts: list[str] = []
            for piece in llm_client.generate_stream(SYSTEM_PROMPT, user_prompt):
                answer_parts.append(piece)
                yield "token", {"text": piece}
            answer = "".join(answer_parts)

        with session_factory() as write_session:
            get_or_create_conversation(write_session, conversation_id, owner_id)
            append_message(write_session, conversation_id, "user", query)
            append_message(write_session, conversation_id, "assistant", answer)
            write_session.commit()

        yield "done", {"conversation_id": str(conversation_id)}
    except Exception:
        logger.exception("Streaming generation failed")
        yield "error", {"detail": "Generation query failed"}


def get_conversation_history(
    conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> ConversationHistoryResponse | None:
    """Return every message in `conversation_id`, oldest first.

    Returns `None` if the conversation doesn't exist, or if it belongs to a different
    `owner_id` -- the caller (router) maps both to a 404.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        if get_conversation(session, conversation_id, owner_id) is None:
            return None
        records = get_all_messages(session, conversation_id)

    messages = [
        Message(role=record.role, content=record.content, created_at=record.created_at)
        for record in records
    ]
    return ConversationHistoryResponse(conversation_id=conversation_id, messages=messages)
```

- [ ] **Step 3: Protect and wire the generation + conversations routers**

Modify `app/generation/router.py`:
```python
"""Generation API: grounded answer synthesis over retrieved chunks."""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.generation.schemas import ConversationHistoryResponse, GenerationQuery, GenerationResponse
from app.generation.service import generate, generate_stream, get_conversation_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/query")
def query(
    query_request: GenerationQuery, current_user: CurrentUser = Depends(get_current_user)
) -> GenerationResponse:
    """Run retrieval + LLM synthesis and return a grounded, cited answer."""
    try:
        return generate(
            query_request.query,
            query_request.top_k,
            current_user.id,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
            conversation_id=query_request.conversation_id,
        )
    except Exception as exc:
        logger.exception("Generation query failed")
        raise HTTPException(status_code=503, detail="Generation query failed") from exc


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _event_stream(query_request: GenerationQuery, owner_id: uuid.UUID) -> Iterator[str]:
    for event, data in generate_stream(
        query_request.query,
        query_request.top_k,
        owner_id,
        rerank=query_request.rerank,
        expand_sections=query_request.expand_sections,
        conversation_id=query_request.conversation_id,
    ):
        yield _format_sse(event, data)


@router.post("/query/stream")
def query_stream(
    query_request: GenerationQuery, current_user: CurrentUser = Depends(get_current_user)
) -> StreamingResponse:
    """Run retrieval + LLM synthesis, streaming the answer as Server-Sent Events.

    Unlike `POST /generation/query`, failures surface as a terminal `error` SSE event
    (status stays 200, since headers are already sent once streaming starts) rather than
    an HTTP error status -- see `generate_stream`'s docstring.
    """
    return StreamingResponse(
        _event_stream(query_request, current_user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@conversations_router.get("/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> ConversationHistoryResponse:
    """Return a conversation's full message history, oldest first, or 404 if unknown or not yours."""
    history = get_conversation_history(conversation_id, current_user.id)
    if history is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return history
```

- [ ] **Step 4: Update existing tests' call sites**

Modify `tests/generation/test_repository.py`: add `_TEST_OWNER_ID = uuid.uuid4()` plus the `_ensure_test_owner` helper (same pattern as Task 12 Step 5); every `get_or_create_conversation(session, conversation_id)` call becomes `get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)`; every `get_conversation(session, conversation_id)` call becomes `get_conversation(session, conversation_id, _TEST_OWNER_ID)`.

Modify `tests/generation/test_service.py`: every `generate(...)` / `generate_stream(...)` / `get_conversation_history(...)` call gains `_TEST_OWNER_ID` (as the 3rd positional argument for `generate`/`generate_stream`, 2nd positional for `get_conversation_history`); every `_fake_search(query, top_k, rerank=False, expand_sections=False)` monkeypatch stub used in place of `retrieval_search` gains an `owner_id` parameter (positional, after `top_k`) since `generate`/`generate_stream` now call it with `owner_id` as a positional argument: `def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False): ...`.

Modify `tests/generation/test_router.py`: add the same `_register_and_login`/`auth_headers` helper as Task 13 Step 5 (a shared version should be extracted to a common conftest — see Step 5 below); add `headers=auth_headers` to every `client.post("/generation/query", ...)`, `client.post("/generation/query/stream", ...)` and `client.get(f"/conversations/{conversation_id}")` call; update every inline `_fake_search`/`lambda *a, **k: [chunk]` monkeypatch of `app.generation.service.retrieval_search` — since it's patched with `*a, **k`, no signature change is needed there (the extra `owner_id` positional argument is silently absorbed by `*a`).

- [ ] **Step 5: Extract the shared auth-test-client helper**

Since Tasks 12–14 each independently added an `auth_headers`/`_register_and_login` helper to their own router test file, consolidate into one shared fixture to avoid drift. Create `tests/auth_helpers.py`:
```python
"""Shared test helper for registering + logging in a throwaway user against a live TestClient."""

import uuid

from fastapi.testclient import TestClient


def register_and_login(client: TestClient, prefix: str) -> dict[str, str]:
    """Register a fresh throwaway user (`prefix` + a UUID, so tests never collide) and log in.

    Returns headers ready to pass as `client.post(..., headers=...)`.
    """
    email = f"{prefix}-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    login_response = client.post("/auth/login", json={"email": email, "password": password})
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
```
Update `tests/ingestion/test_router.py`, `tests/retrieval/test_router.py`, and `tests/generation/test_router.py` to import and use `register_and_login(client, "<module-specific-prefix>")` instead of each file's own inline duplicate (replacing the `auth_headers` fixture body with `return register_and_login(client, "ingestion")` etc., and the `_register_and_login` helper from Task 13 Step 5 with a thin wrapper calling this shared one).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS (every test in the suite, including all of `tests/auth/`, `tests/ingestion/`, `tests/retrieval/`, `tests/generation/`)

- [ ] **Step 7: Commit**

```bash
git add app/generation tests/generation tests/ingestion tests/retrieval tests/auth_helpers.py
git commit -m "feat: enforce per-user isolation in generation and conversations (ERP-026)"
```

---

### Task 15: Coverage, lint, type-check, and full verification

**Files:**
- No new files expected; this task is verification-only, with fixes applied wherever a check fails.

- [ ] **Step 1: Run the full suite with coverage**

Run: `uv run pytest --cov=app --cov-report=term-missing`
Expected: all tests pass, coverage stays `>= 90%`. If any new module (`app/auth/*`) is under-covered, add the missing test case(s) to the corresponding `tests/auth/test_*.py` file before proceeding.

- [ ] **Step 2: Run Ruff**

Run: `uv run ruff check .`
Expected: no findings. Fix any docstring (`D`), import-order (`I`), or blanket-except (`BLE`) violations in the new `app/auth/*` files.

- [ ] **Step 3: Run Mypy**

Run: `uv run mypy app/`
Expected: no errors. `app/auth/*` must be fully typed (no implicit `Any`, no un-annotated returns) to satisfy `--strict`.

- [ ] **Step 4: Verify the pre-commit hooks pass**

Run: `uv run pre-commit run --all-files`
Expected: gitleaks, ruff, and any other configured hooks all pass (no secrets detected — double-check the migration's hardcoded `_SYSTEM_USER_HASH = "!"` string doesn't trip gitleaks; it's a deliberately-invalid placeholder, not a real secret, but confirm the scanner agrees).

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address lint/type/coverage findings from ERP-026 verification pass"
```
(Skip this commit entirely if Steps 1-4 all passed clean with no changes needed.)

---

### Task 16: Ticket file + `current-state.md` update

**Files:**
- Create: `.ai/tickets/ERP-026.md`
- Modify: `.ai/memory/current-state.md`
- Create: `.ai/sessions/2026-09-05-authentication.md` (or the actual completion date, if different)

**Interfaces:** None — documentation/process only.

- [ ] **Step 1: Write `.ai/tickets/ERP-026.md`**

```markdown
# ERP-026 — Authentication

Status: Done
Depends On: None

## Description

Adds local email/password authentication (JWT access + rotating refresh tokens), `admin`/`user` role authorization, and per-user data isolation (documents, chunks-via-document, conversations) across every existing router. Closes the "Authentication" gap named in `docs/roadmap.md` and `docs/architecture.md`'s Project Goals, and completes ADR-003's third named Redis use (session/auth-token cache — the refresh-token revocation cache).

## Acceptance Criteria

- [x] `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout` implemented
- [x] `users` and `refresh_tokens` tables added via Alembic migration; `documents`/`conversations` gain a required `owner_id`, backfilled to a fixed `system` user for any pre-existing rows
- [x] Every existing endpoint (`ingestion`, `retrieval`, `generation`, `conversations`) requires a valid access token via `get_current_user`
- [x] Retrieval, ingestion job status, and conversation history are scoped to the caller's own data; wrong-owner access returns 404
- [x] Refresh-token rotation-on-use with a Redis-backed revocation cache (cache-aside, never load-bearing)
- [x] `ruff`, `mypy --strict`, and `pytest-cov --cov-fail-under=90` all pass

## Notes

Design spec: `docs/superpowers/specs/2026-09-05-authentication-design.md`. Deferred follow-ups (logged per user instruction during brainstorming): per-tenant/partitioned FAISS index, external IdP/OAuth2/OIDC integration, admin user-management endpoints, self-service admin account creation. See the spec's "Deferred / Future Follow-ups" section for detail on each.
```

- [ ] **Step 2: Update `.ai/memory/current-state.md`**

Add a new bullet to the "What Exists" section (following the existing style of prior ERP entries) summarizing what Task 1-14 built, and update "Next Planned Work" to:
- Remove the "blocked on a future Authentication ticket" language from the session/auth-token cache bullet (now built) and from the per-user cache-key-scoping bullet (now built via the `owner_id`-inclusive cache key in Task 13).
- Add the four deferred follow-ups from the spec (per-tenant/partitioned FAISS index, external IdP/OAuth2/OIDC, admin user-management endpoints, self-service admin creation) as new explicit bullets, per [[feedback_track_deferred_followups]] — so they're findable without re-reading this ticket's spec.

- [ ] **Step 3: Write the session log**

Use `.ai/templates/session.md` as the template; summarize what was decided (per the design spec) and built (per Tasks 1-15), saved to `.ai/sessions/<actual-date>-authentication.md`.

- [ ] **Step 4: Commit**

```bash
git add .ai/tickets/ERP-026.md .ai/memory/current-state.md .ai/sessions/
git commit -m "docs: close out ERP-026 (authentication)"
```

---

## Execution Handoff

After this plan is saved, the two ways to run it are:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, with a two-stage review between tasks.
2. **Inline Execution** — batch execution in this session with checkpoints for review.
