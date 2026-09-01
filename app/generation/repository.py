"""Persistence for multi-turn conversations."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.models import ConversationMessageRecord, ConversationRecord


def get_or_create_conversation(session: Session, conversation_id: uuid.UUID) -> ConversationRecord:
    """Return the `ConversationRecord` for `conversation_id`, creating it if it doesn't exist yet.

    Does not commit -- the caller controls the transaction boundary.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    if conversation is None:
        conversation = ConversationRecord(id=conversation_id)
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
        .order_by(ConversationMessageRecord.created_at.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))
