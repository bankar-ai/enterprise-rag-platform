import uuid
from datetime import datetime, timedelta, timezone

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


def test_get_user_by_id_round_trip():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, "by-id-test@example.com", "hashed")
        session.commit()

        found = get_user_by_id(session, user.id)
        assert found is not None
        assert found.email == "by-id-test@example.com"


def test_get_user_by_id_returns_none_for_unknown_id():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_user_by_id(session, uuid.uuid4()) is None


def test_list_users_includes_created_user():
    session_factory = get_session_factory()
    with session_factory() as session:
        create_user(session, "list-repo-test@example.com", "hashed")
        session.commit()

        emails = [user.email for user in list_users(session)]
        assert "list-repo-test@example.com" in emails


def test_set_user_active_toggles_flag():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, "active-repo-test@example.com", "hashed")
        session.commit()
        assert user.is_active is True

        set_user_active(session, user, False)
        session.commit()

        found = get_user_by_id(session, user.id)
        assert found is not None
        assert found.is_active is False


def test_revoke_all_refresh_tokens_for_user_revokes_only_that_users_tokens():
    session_factory = get_session_factory()
    with session_factory() as session:
        user_a = create_user(session, "revoke-a@example.com", "hashed")
        user_b = create_user(session, "revoke-b@example.com", "hashed")
        session.flush()
        expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        create_refresh_token(session, user_a.id, "revoke-a-hash", expires_at)
        create_refresh_token(session, user_b.id, "revoke-b-hash", expires_at)
        session.commit()

        revoke_all_refresh_tokens_for_user(session, user_a.id)
        session.commit()

        assert get_refresh_token_by_hash(session, "revoke-a-hash").revoked_at is not None
        assert get_refresh_token_by_hash(session, "revoke-b-hash").revoked_at is None
