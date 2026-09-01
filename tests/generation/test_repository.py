import uuid

from app.core.db import get_session_factory
from app.generation.repository import append_message, get_or_create_conversation, get_recent_messages


def test_get_or_create_conversation_creates_new_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        conversation = get_or_create_conversation(session, conversation_id)
        session.commit()

        assert conversation.id == conversation_id


def test_get_or_create_conversation_returns_existing_row_without_duplicating():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        session.commit()

    with session_factory() as session:
        conversation = get_or_create_conversation(session, conversation_id)
        session.commit()

        assert conversation.id == conversation_id


def test_append_message_persists_role_and_content():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        message = append_message(session, conversation_id, "user", "hello there")
        session.commit()

        assert message.role == "user"
        assert message.content == "hello there"
        assert message.conversation_id == conversation_id


def test_get_recent_messages_returns_oldest_first():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
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
        get_or_create_conversation(session, conversation_id)
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
