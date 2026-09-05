import uuid

from app.core.db import get_session_factory
from app.generation.repository import (
    append_message,
    get_all_messages,
    get_conversation,
    get_or_create_conversation,
    get_recent_messages,
)

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


def test_get_or_create_conversation_creates_new_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        conversation = get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

        assert conversation.id == conversation_id


def test_get_or_create_conversation_returns_existing_row_without_duplicating():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        conversation = get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

        assert conversation.id == conversation_id


def test_append_message_persists_role_and_content():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        message = append_message(session, conversation_id, "user", "hello there")
        session.commit()

        assert message.role == "user"
        assert message.content == "hello there"
        assert message.conversation_id == conversation_id


def test_get_recent_messages_returns_oldest_first():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "first")
        append_message(session, conversation_id, "assistant", "second")
        append_message(session, conversation_id, "user", "third")
        session.commit()

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)

        assert [m.content for m in messages] == ["first", "second", "third"]


def test_get_recent_messages_respects_limit_keeping_most_recent():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "first")
        append_message(session, conversation_id, "assistant", "second")
        append_message(session, conversation_id, "user", "third")
        session.commit()

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=2)

        assert [m.content for m in messages] == ["second", "third"]


def test_get_recent_messages_unknown_conversation_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_recent_messages(session, uuid.uuid4(), limit=10) == []


def test_get_conversation_returns_existing_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        conversation = get_conversation(session, conversation_id, _TEST_OWNER_ID)

        assert conversation is not None
        assert conversation.id == conversation_id


def test_get_conversation_unknown_id_returns_none():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_conversation(session, uuid.uuid4(), _TEST_OWNER_ID) is None


def test_get_conversation_does_not_create_a_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_conversation(session, conversation_id, _TEST_OWNER_ID)

    with session_factory() as session:
        assert get_conversation(session, conversation_id, _TEST_OWNER_ID) is None


def test_get_all_messages_returns_every_message_oldest_first_no_limit():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        for i in range(12):
            append_message(session, conversation_id, "user", f"message {i}")
        session.commit()

    with session_factory() as session:
        messages = get_all_messages(session, conversation_id)

        assert [m.content for m in messages] == [f"message {i}" for i in range(12)]


def test_get_all_messages_unknown_conversation_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_all_messages(session, uuid.uuid4()) == []
