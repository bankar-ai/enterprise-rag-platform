"""SQLAlchemy ORM models for users and refresh tokens."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class UserRecord(Base):
    """A registered user, identified by email."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    # Nullable: an OIDC-only user (see OidcIdentityRecord) never sets a local password. `login()`
    # explicitly checks for `None` before verifying, so such a user simply can't authenticate via
    # POST /auth/login -- correct, since they never had a password to check.
    hashed_password: Mapped[str | None] = mapped_column(default=None)
    role: Mapped[str] = mapped_column(default="user")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
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


class OidcIdentityRecord(Base):
    """A single external OIDC identity linked to a `UserRecord`.

    A separate table (rather than `auth_provider`/`external_id` columns on `users`) so one user
    row can hold a local password *and* one or more linked OIDC identities at the same time --
    required so OIDC login is additive, not a replacement for local login (ERP-032). Lookups by
    `(provider, external_id)` are the only ones performed after the initial link; `email` is
    kept only as an audit trail of what was claimed at link time.
    """

    __tablename__ = "oidc_identities"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_oidc_identities_provider_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    provider: Mapped[str]
    external_id: Mapped[str]
    email: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
