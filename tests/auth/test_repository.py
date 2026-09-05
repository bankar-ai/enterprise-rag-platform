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
