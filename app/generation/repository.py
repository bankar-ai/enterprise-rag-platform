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
