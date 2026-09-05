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
