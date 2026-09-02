# Conversation Memory (ERP-018) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /generation/query` multi-turn: an optional `conversation_id` triggers Postgres-backed history storage, LLM-based query rewriting for retrieval, and conversation-aware answer generation — while an omitted `conversation_id` keeps today's (ERP-017) behavior byte-for-byte unchanged.

**Architecture:** Two new Postgres tables (`conversations`, `conversation_messages`) via SQLAlchemy + Alembic. `app/generation/service.py`'s `generate()` branches at the top: no `conversation_id` → the exact ERP-017 code path (no DB session, no rewrite, nothing persisted); a `conversation_id` given → load recent history, rewrite the query for retrieval when history exists, generate with history in the prompt, then persist both turns in one transaction after generation succeeds.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2.0 + Alembic, `ollama` (already a dependency), pytest, `monkeypatch`, a real Postgres test database (already used by `tests/ingestion` and `tests/retrieval`).

**Spec:** `docs/superpowers/specs/2026-09-01-conversation-memory-design.md`

## Global Constraints

- All dependencies via `uv add`/`uv sync` — no `pip install`. This plan introduces **no new dependency**.
- No `print()` in application code — use `logging` with a module-level `logger = logging.getLogger(__name__)` where logging is needed.
- 90% coverage gate applies (CI `--cov-fail-under=90`) — every new/modified module needs tests exercising its branches.
- Business logic never lives in API routes — `app/generation/router.py` only validates request, calls `generate()`, returns/raises. No history loading, rewriting, or persistence logic in the router.
- `conversation_id` is a **stateless/stateful switch**: omitted (`null`) → no session opened, no rewrite, nothing persisted, `response.conversation_id = null` (byte-for-byte ERP-017 behavior — every existing test in `tests/generation/test_service.py` and `tests/generation/test_router.py` must keep passing unmodified). Provided → client-supplied UUID, get-or-create semantics (no "unknown ID" error case, no 404).
- No new dependency, no new endpoint, no authentication/ownership of conversations — all explicitly out of scope per the spec.

---

## Task 1: Conversation & message ORM models, settings, migration

**Files:**
- Create: `app/generation/models.py`
- Modify: `app/generation/config.py`
- Modify: `alembic/env.py`
- Modify: `tests/generation/conftest.py` (delete — see Step 6)
- Create: (via `alembic revision`) `alembic/versions/<generated>_create_conversations_and_conversation_messages_tables.py`

**Interfaces:**
- Produces: `ConversationRecord` (`id: Mapped[uuid.UUID]` primary key, no default — always set explicitly by the caller; `created_at: Mapped[datetime]`), `ConversationMessageRecord` (`id: Mapped[uuid.UUID]` primary key with `default=uuid.uuid4`; `conversation_id: Mapped[uuid.UUID]` FK to `conversations.id`, indexed; `role: Mapped[str]`; `content: Mapped[str]` (`Text`); `created_at: Mapped[datetime]`) in `app/generation/models.py`, both using `Base` imported from `app.ingestion.models`. `GenerationSettings.history_window_turns: int = 6`.

- [ ] **Step 1: Create `app/generation/models.py`**

```python
"""SQLAlchemy ORM models for multi-turn conversation memory (ERP-018)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class ConversationRecord(Base):
    """A single multi-turn conversation. `id` is always client-supplied, never generated here."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ConversationMessageRecord(Base):
    """A single turn (`role` is `"user"` or `"assistant"`) within a conversation."""

    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

- [ ] **Step 2: Add `history_window_turns` to `GenerationSettings`**

In `app/generation/config.py`, add one field to the existing `GenerationSettings` class (after `temperature`):

```python
    history_window_turns: int = 6
```

Full class after the edit:

```python
class GenerationSettings(BaseSettings):
    """Configuration for LLM-backed answer generation.

    Overridable via `GENERATION_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="GENERATION_")

    ollama_host: str = "http://localhost:11434"
    model: str = "qwen3"
    max_context_chars: int = 8000
    temperature: float = 0.1
    history_window_turns: int = 6
```

- [ ] **Step 3: Register the new models with Alembic's target metadata**

In `alembic/env.py`, add one import line near the top (alongside the existing `from app.ingestion.models import Base`):

```python
import app.generation.models  # noqa: F401  -- registers ConversationRecord/ConversationMessageRecord on Base.metadata
```

- [ ] **Step 4: Generate and fill in the Alembic migration**

Run: `uv run alembic revision -m "create conversations and conversation messages tables"`

This prints the path to a new file under `alembic/versions/` with an auto-assigned `revision` and `down_revision` already filled in (pointing at the current head, `a97a8780506f`). Open that file and replace its `upgrade()`/`downgrade()` bodies with:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'conversation_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conversation_messages_conversation_id'),
        'conversation_messages',
        ['conversation_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_conversation_messages_conversation_id'), table_name='conversation_messages')
    op.drop_table('conversation_messages')
    op.drop_table('conversations')
```

Add this import near the top of the file, alongside the existing `import sqlalchemy as sa`:

```python
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 5: Verify the migration applies**

Ensure the local Postgres dev container is running (`docker compose up -d postgres`), then run:

`uv run alembic upgrade head`

Expected: no errors; `alembic current` then reports the new revision as head. Run `uv run alembic downgrade -1` followed by `uv run alembic upgrade head` once more to confirm `downgrade()` is also correct (leaves no error, and `upgrade()` re-applies cleanly).

- [ ] **Step 6: Delete `tests/generation/conftest.py`**

This file currently overrides the root `tests/conftest.py`'s session-scoped `_database_schema` fixture as a no-op (added in ERP-017 Task 1, when generation tests didn't need a database). Conversation memory tests do need real tables, and `ConversationRecord`/`ConversationMessageRecord` will already be registered on `Base.metadata` by pytest's collection phase (before any fixture runs) once later tasks add test files that import them — so deleting this override is sufficient; no other conftest change is needed.

Delete the file: `tests/generation/conftest.py`.

- [ ] **Step 7: Verify existing generation tests still pass against a real database**

Run: `uv run pytest tests/generation/ -v`

Expected: all existing tests (from ERP-017) still PASS — they were written against fakes/stubs and never depended on the DB being absent, so removing the no-op override should not break them. If any test unexpectedly fails, investigate before proceeding (do not paper over a real regression).

- [ ] **Step 8: Commit**

```bash
git add app/generation/models.py app/generation/config.py alembic/env.py alembic/versions/*_create_conversations_and_conversation_messages_tables.py tests/generation/conftest.py
git commit -m "feat: add conversation/message ORM models and migration for ERP-018"
```

---

## Task 2: Conversation repository

**Files:**
- Create: `app/generation/repository.py`
- Test: `tests/generation/test_repository.py`

**Interfaces:**
- Consumes: `ConversationRecord`, `ConversationMessageRecord` from `app.generation.models` (Task 1).
- Produces: `get_or_create_conversation(session: Session, conversation_id: uuid.UUID) -> ConversationRecord`, `append_message(session: Session, conversation_id: uuid.UUID, role: str, content: str) -> ConversationMessageRecord`, `get_recent_messages(session: Session, conversation_id: uuid.UUID, limit: int) -> list[ConversationMessageRecord]` (oldest-first) in `app/generation/repository.py`. None of these commit — callers control the transaction boundary (same convention as `app/ingestion/repository.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/generation/test_repository.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generation/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.repository'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/repository.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_repository.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/repository.py tests/generation/test_repository.py
git commit -m "feat: add conversation repository for ERP-018"
```

---

## Task 3: Schema changes — `conversation_id` and `ConversationTurn`

**Files:**
- Modify: `app/generation/schemas.py`
- Modify: `tests/generation/test_schemas.py`

**Interfaces:**
- Produces: `GenerationQuery.conversation_id: uuid.UUID | None = Field(default=None)`; `ConversationTurn` (`role: str`, `content: str`) — a plain Pydantic model decoupling `app/generation/prompt.py` and `app/generation/rewrite.py` (Tasks 4-5) from the ORM, the same way `RetrievedChunk` already decouples `app/generation/prompt.py` from `app/ingestion/models.ChunkRecord`; `GenerationResponse.conversation_id: uuid.UUID | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/generation/test_schemas.py` (append to the existing file, keep all existing tests unchanged):

```python
import uuid


def test_generation_query_conversation_id_defaults_to_none():
    query = GenerationQuery(query="hello")
    assert query.conversation_id is None


def test_generation_query_accepts_conversation_id():
    conversation_id = uuid.uuid4()
    query = GenerationQuery(query="hello", conversation_id=conversation_id)
    assert query.conversation_id == conversation_id


def test_generation_response_conversation_id_defaults_to_none():
    response = GenerationResponse(answer="hi", citations=[])
    assert response.conversation_id is None


def test_generation_response_accepts_conversation_id():
    conversation_id = uuid.uuid4()
    response = GenerationResponse(answer="hi", citations=[], conversation_id=conversation_id)
    assert response.conversation_id == conversation_id


def test_conversation_turn_round_trip():
    from app.generation.schemas import ConversationTurn

    turn = ConversationTurn(role="user", content="hello")
    assert turn.model_dump() == {"role": "user", "content": "hello"}
```

Move the `import uuid` line to the top of the file with the other imports instead of inline, matching the file's existing style.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generation/test_schemas.py -v`
Expected: FAIL — `conversation_id` is not a recognized field on `GenerationQuery`/`GenerationResponse`, and `ConversationTurn` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Replace the full contents of `app/generation/schemas.py` with:

```python
"""Pydantic schemas for the generation API's request and response."""

import uuid

from pydantic import BaseModel, Field


class GenerationQuery(BaseModel):
    """A grounded-answer generation request.

    `conversation_id` is a stateless/stateful switch: omitted (`None`) keeps this request
    fully stateless -- no history loaded, nothing persisted, matching the original
    single-turn behavior exactly. Provided, it is a client-supplied UUID: if no
    conversation with that ID exists yet, one is created; if it does, it is continued.
    """

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = Field(default=False)
    expand_sections: bool = Field(default=False)
    conversation_id: uuid.UUID | None = Field(default=None)


class ConversationTurn(BaseModel):
    """One turn of conversation history, decoupled from how it's persisted."""

    role: str
    content: str


class Citation(BaseModel):
    """Provenance for one chunk that was included in the answer's context."""

    chunk_id: str
    document_id: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str


class GenerationResponse(BaseModel):
    """A synthesized answer with the citations backing its inline [n] markers.

    `conversation_id` is `None` for a stateless request, otherwise the conversation's ID
    (echoed back, or newly created on this call).
    """

    answer: str
    citations: list[Citation]
    conversation_id: uuid.UUID | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_schemas.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/schemas.py tests/generation/test_schemas.py
git commit -m "feat: add conversation_id and ConversationTurn schemas for ERP-018"
```

---

## Task 4: Query rewriting

**Files:**
- Create: `app/generation/rewrite.py`
- Test: `tests/generation/test_rewrite.py`

**Interfaces:**
- Consumes: `LLMClient` from `app.generation.client` (exact interface: `generate(self, system_prompt: str, user_prompt: str) -> str`); `ConversationTurn` from `app.generation.schemas` (Task 3).
- Produces: `REWRITE_SYSTEM_PROMPT: str`; `rewrite_query(query: str, history: list[ConversationTurn], llm_client: LLMClient) -> str` in `app/generation/rewrite.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/generation/test_rewrite.py`:

```python
from app.generation.rewrite import rewrite_query
from app.generation.schemas import ConversationTurn


class _FakeLLMClient:
    def __init__(self, answer):
        self._answer = answer
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._answer


def test_rewrite_query_includes_history_and_question_in_prompt():
    history = [
        ConversationTurn(role="user", content="what is the deployment process?"),
        ConversationTurn(role="assistant", content="it has three steps: build, test, deploy."),
    ]
    fake_llm = _FakeLLMClient("  what is the second step in the deployment process?  ")

    result = rewrite_query("what about the second one?", history, fake_llm)

    assert result == "what is the second step in the deployment process?"
    assert len(fake_llm.calls) == 1
    system_prompt, user_prompt = fake_llm.calls[0]
    assert "rephrase" in system_prompt.lower()
    assert "standalone" in system_prompt.lower()
    assert "user: what is the deployment process?" in user_prompt.lower()
    assert "assistant: it has three steps" in user_prompt.lower()
    assert "what about the second one?" in user_prompt.lower()


def test_rewrite_query_strips_whitespace_from_llm_response():
    history = [ConversationTurn(role="user", content="hi")]
    fake_llm = _FakeLLMClient("\n  already standalone question?  \n")

    result = rewrite_query("already standalone question?", history, fake_llm)

    assert result == "already standalone question?"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generation/test_rewrite.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generation.rewrite'`

- [ ] **Step 3: Write minimal implementation**

Create `app/generation/rewrite.py`:

```python
"""LLM-based query rewriting for retrieval, using conversation history."""

from app.generation.client import LLMClient
from app.generation.schemas import ConversationTurn

REWRITE_SYSTEM_PROMPT = (
    "Given a conversation history and a follow-up question, rephrase the follow-up "
    "question into a standalone question that can be understood without the conversation "
    "history. Do not answer the question -- only rephrase it. If the follow-up question "
    "is already standalone, return it unchanged. Reply with only the rephrased question, "
    "nothing else."
)


def rewrite_query(query: str, history: list[ConversationTurn], llm_client: LLMClient) -> str:
    """Rephrase `query` into a standalone retrieval query using `history`.

    Only meaningful when `history` is non-empty -- callers should skip invoking this
    entirely for a conversation's first turn (see `app/generation/service.py`).
    """
    history_lines = [f"{turn.role}: {turn.content}" for turn in history]
    history_block = "\n".join(history_lines)
    user_prompt = f"Conversation history:\n{history_block}\n\nFollow-up question: {query}"
    return llm_client.generate(REWRITE_SYSTEM_PROMPT, user_prompt).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_rewrite.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generation/rewrite.py tests/generation/test_rewrite.py
git commit -m "feat: add LLM-based query rewriting for ERP-018"
```

---

## Task 5: `build_prompt` gains conversation history

**Files:**
- Modify: `app/generation/prompt.py`
- Modify: `tests/generation/test_prompt.py`

**Interfaces:**
- Consumes: `ConversationTurn` from `app.generation.schemas` (Task 3).
- Produces: `build_prompt(query: str, chunks: list[RetrievedChunk], max_context_chars: int, history: list[ConversationTurn] | None = None) -> tuple[str, list[RetrievedChunk]]` — same return shape as before; `history` renders as a chronological transcript block before the numbered context block. Existing 2-arg-after-`max_context_chars` call sites (i.e. calls that don't pass `history`) are unaffected: `history=None` produces output byte-identical to before this task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/generation/test_prompt.py` (append to the existing file; keep all existing tests unchanged and passing):

```python
from app.generation.schemas import ConversationTurn


def test_build_prompt_renders_history_before_context():
    chunks = [_chunk("c1", "First chunk text.")]
    history = [
        ConversationTurn(role="user", content="what is the deployment process?"),
        ConversationTurn(role="assistant", content="it has three steps."),
    ]

    user_prompt, included = build_prompt(
        "what about the second one?", chunks, max_context_chars=1000, history=history
    )

    history_index = user_prompt.index("user: what is the deployment process?")
    assistant_index = user_prompt.index("assistant: it has three steps.")
    context_index = user_prompt.index("[1] First chunk text.")
    question_index = user_prompt.index("Question: what about the second one?")
    assert history_index < assistant_index < context_index < question_index
    assert included == chunks


def test_build_prompt_with_no_history_matches_omitted_history():
    chunks = [_chunk("c1", "First chunk text.")]

    with_none, included_a = build_prompt("q", chunks, max_context_chars=1000, history=None)
    omitted, included_b = build_prompt("q", chunks, max_context_chars=1000)

    assert with_none == omitted
    assert included_a == included_b == chunks


def test_build_prompt_with_empty_history_list_matches_omitted_history():
    chunks = [_chunk("c1", "First chunk text.")]

    with_empty, _ = build_prompt("q", chunks, max_context_chars=1000, history=[])
    omitted, _ = build_prompt("q", chunks, max_context_chars=1000)

    assert with_empty == omitted
```

Add `role` rendering as lowercase (`"user: ..."`, `"assistant: ..."`) — match the test assertions above exactly (they check for lowercase `"user: "`/`"assistant: "` substrings via `.index`, which is case-sensitive).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generation/test_prompt.py -v`
Expected: FAIL — `build_prompt()` does not accept a `history` keyword argument yet.

- [ ] **Step 3: Write minimal implementation**

Replace the full contents of `app/generation/prompt.py` with:

```python
"""Prompt construction for LLM-backed answer generation."""

from app.generation.schemas import ConversationTurn
from app.retrieval.schemas import RetrievedChunk

SYSTEM_PROMPT = (
    "You are an assistant answering questions using only the provided context. "
    "Cite sources inline using [1], [2], etc. matching the numbered context below. "
    "If the context does not contain enough information to answer, say so explicitly "
    "-- do not use outside knowledge."
)


def build_prompt(
    query: str,
    chunks: list[RetrievedChunk],
    max_context_chars: int,
    history: list[ConversationTurn] | None = None,
) -> tuple[str, list[RetrievedChunk]]:
    """Build the numbered-context user prompt, truncated to `max_context_chars`.

    Walks `chunks` in order, accumulating character count. The first chunk is always
    included even if it alone exceeds the budget (so a single oversized top result
    doesn't produce empty context); every subsequent chunk is included only if adding
    it would not exceed `max_context_chars`. Returns the user-prompt text and the list
    of chunks actually included, in citation-number order.

    `history`, if given, is rendered as a chronological transcript before the numbered
    context block -- omitted or `[]` produces output identical to no `history` argument
    at all.
    """
    included: list[RetrievedChunk] = []
    total_chars = 0
    for chunk in chunks:
        entry_len = len(chunk.text)
        if included and total_chars + entry_len > max_context_chars:
            break
        included.append(chunk)
        total_chars += entry_len

    context_lines = []
    for index, chunk in enumerate(included, start=1):
        section = " > ".join(chunk.section_path) if chunk.section_path else "N/A"
        context_lines.append(
            f"[{index}] {chunk.text}\n(source: {chunk.source_filename}, section: {section})"
        )
    context_block = "\n\n".join(context_lines)

    history_lines = [f"{turn.role}: {turn.content}" for turn in history or []]
    history_block = "\n".join(history_lines)

    parts = [part for part in (history_block, context_block, f"Question: {query}") if part]
    user_prompt = "\n\n".join(parts)
    return user_prompt, included
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_prompt.py -v`
Expected: PASS (7 passed — 4 existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add app/generation/prompt.py tests/generation/test_prompt.py
git commit -m "feat: add conversation history rendering to build_prompt for ERP-018"
```

---

## Task 6: `service.generate` orchestration

**Files:**
- Modify: `app/generation/service.py`
- Modify: `tests/generation/test_service.py`

**Interfaces:**
- Consumes: `get_session_factory` from `app.core.db`; `get_or_create_conversation`/`append_message`/`get_recent_messages` from `app.generation.repository` (Task 2); `ConversationTurn` from `app.generation.schemas` (Task 3); `rewrite_query` from `app.generation.rewrite` (Task 4); `build_prompt(..., history=...)` from `app.generation.prompt` (Task 5).
- Produces: `generate(query: str, top_k: int, rerank: bool = False, expand_sections: bool = False, conversation_id: uuid.UUID | None = None, settings: GenerationSettings | None = None, llm_client: LLMClient | None = None) -> GenerationResponse`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/generation/test_service.py` (append; keep all existing tests unchanged and passing — they exercise the `conversation_id=None` path and must not need modification):

```python
import uuid

from app.core.db import get_session_factory
from app.generation.repository import get_or_create_conversation, get_recent_messages


def test_generate_with_new_conversation_id_creates_conversation_and_persists_turns(monkeypatch):
    conversation_id = uuid.uuid4()
    chunks = [_chunk("c1")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1]")

    response = generate(
        "what is X?", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
    )

    assert response.conversation_id == conversation_id
    assert response.answer == "the answer [1]"

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "what is X?"
    assert messages[1].content == "the answer [1]"


def test_generate_first_turn_of_conversation_does_not_call_rewrite(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")]
    )
    rewrite_calls = []
    monkeypatch.setattr(
        "app.generation.service.rewrite_query",
        lambda *a, **k: rewrite_calls.append(a) or "should not be reached",
    )
    fake_llm = _FakeLLMClient("answer")

    generate("what is X?", top_k=5, conversation_id=conversation_id, llm_client=fake_llm)

    assert rewrite_calls == []


def test_generate_second_turn_rewrites_query_using_history(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        from app.generation.repository import append_message

        append_message(session, conversation_id, "user", "what is the deployment process?")
        append_message(session, conversation_id, "assistant", "it has three steps.")
        session.commit()

    captured_retrieval_query = {}

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        captured_retrieval_query["query"] = query
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    monkeypatch.setattr(
        "app.generation.service.rewrite_query",
        lambda query, history, llm_client: "what is the second step in the deployment process?",
    )
    fake_llm = _FakeLLMClient("the second step is test.")

    response = generate(
        "what about the second one?",
        top_k=5,
        conversation_id=conversation_id,
        llm_client=fake_llm,
    )

    assert captured_retrieval_query["query"] == "what is the second step in the deployment process?"
    assert response.conversation_id == conversation_id

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == [
        "what is the deployment process?",
        "it has three steps.",
        "what about the second one?",
        "the second step is test.",
    ]


def test_generate_conversation_rewrite_failure_propagates_and_commits_nothing(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        from app.generation.repository import append_message

        append_message(session, conversation_id, "user", "first question")
        append_message(session, conversation_id, "assistant", "first answer")
        session.commit()

    def _raise_rewrite(query, history, llm_client):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("app.generation.service.rewrite_query", _raise_rewrite)
    monkeypatch.setattr(
        "app.generation.service.retrieval_search",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retrieval should not run")),
    )
    fake_llm = _FakeLLMClient("should not be reached")

    try:
        generate(
            "a follow-up", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
        )
        raise AssertionError("expected RuntimeError to propagate")
    except RuntimeError as exc:
        assert str(exc) == "ollama unreachable"

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == ["first question", "first answer"]


def test_generate_conversation_short_circuit_still_persists_turns(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeLLMClient("should not be used")

    response = generate(
        "unanswerable question", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
    )

    assert response.answer == NO_CONTEXT_ANSWER
    assert fake_llm.calls == []

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == NO_CONTEXT_ANSWER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generation/test_service.py -v`
Expected: FAIL — `generate()` does not accept a `conversation_id` keyword argument yet.

- [ ] **Step 3: Write minimal implementation**

Replace the full contents of `app/generation/service.py` with:

```python
"""Grounded answer generation over hybrid-retrieved chunks, with optional multi-turn memory."""

import uuid

from app.core.db import get_session_factory
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.repository import append_message, get_or_create_conversation, get_recent_messages
from app.generation.rewrite import rewrite_query
from app.generation.schemas import Citation, ConversationTurn, GenerationResponse
from app.retrieval.schemas import RetrievedChunk
from app.retrieval.service import search as retrieval_search

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
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResponse:
    """Retrieve context for `query` and synthesize a grounded, citation-marked answer.

    `conversation_id` is a stateless/stateful switch. `None` (the default) is fully
    stateless: no session opened, no history loaded, no rewriting, nothing persisted --
    behavior is identical to the single-turn-only version of this function. Given a
    `conversation_id`, the last `settings.history_window_turns` messages are loaded (empty
    on a conversation's first turn); if any exist, `query` is rewritten into a standalone
    retrieval query via `rewrite_query` before running retrieval, and that history is
    rendered into the generation prompt. Both the raw user turn and the assistant's answer
    are persisted in one transaction, but only after generation succeeds -- a failure
    commits nothing.

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
        chunks = retrieval_search(query, top_k, rerank=rerank, expand_sections=expand_sections)
        if not chunks:
            return GenerationResponse(answer=NO_CONTEXT_ANSWER, citations=[], conversation_id=None)

        llm_client = llm_client or OllamaLLMClient(settings)
        user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        return GenerationResponse(
            answer=answer, citations=_citations_for(included_chunks), conversation_id=None
        )

    session_factory = get_session_factory()
    with session_factory() as session:
        history_records = get_recent_messages(session, conversation_id, settings.history_window_turns)
        history = [ConversationTurn(role=r.role, content=r.content) for r in history_records]

        if history:
            llm_client = llm_client or OllamaLLMClient(settings)
            rewritten_query = rewrite_query(query, history, llm_client)
        else:
            rewritten_query = query

        chunks = retrieval_search(rewritten_query, top_k, rerank=rerank, expand_sections=expand_sections)
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

        get_or_create_conversation(session, conversation_id)
        append_message(session, conversation_id, "user", query)
        append_message(session, conversation_id, "assistant", answer)
        session.commit()

    return GenerationResponse(answer=answer, citations=citations, conversation_id=conversation_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_service.py -v`
Expected: PASS (all existing tests + 5 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/generation/service.py tests/generation/test_service.py
git commit -m "feat: wire conversation memory into generate() orchestration for ERP-018"
```

---

## Task 7: Router passthrough and end-to-end tests

**Files:**
- Modify: `app/generation/router.py`
- Modify: `tests/generation/test_router.py`

**Interfaces:**
- Consumes: `generate(..., conversation_id=...)` from `app.generation.service` (Task 6).
- Produces: no new interfaces — `router.py`'s only change is passing `query_request.conversation_id` through.

- [ ] **Step 1: Write the failing test**

Add to `tests/generation/test_router.py` (append; keep all existing tests unchanged and passing):

```python
def test_query_omitted_conversation_id_returns_null_conversation_id():
    response = client.post("/generation/query", json={"query": "anything"})
    assert response.status_code == 200
    assert response.json()["conversation_id"] is None


def test_query_with_conversation_id_continues_across_two_calls(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    conversation_id = str(uuid.uuid4())
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="deployment has three steps",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    retrieval_queries = []

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        retrieval_queries.append(query)
        return [chunk]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)

    from app.generation.client import OllamaLLMClient

    answers = iter(["it has three steps.", "the second step is test."])
    monkeypatch.setattr(
        OllamaLLMClient,
        "generate",
        lambda self, system_prompt, user_prompt: next(answers),
    )

    first = client.post(
        "/generation/query",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
    )
    assert first.status_code == 200
    assert first.json()["conversation_id"] == conversation_id

    second = client.post(
        "/generation/query",
        json={"query": "what about the second one?", "conversation_id": conversation_id},
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    assert retrieval_queries[0] == "what is the deployment process?"
    assert retrieval_queries[1] != "what about the second one?"
```

Note: the second call's real rewrite goes through `OllamaLLMClient.generate`, which is monkeypatched here to return canned answers regardless of its prompt — so `retrieval_queries[1]` will be whatever that canned string is (`"it has three steps."`, the first queued answer, consumed by the rewrite call before the second queued answer is consumed by the final generation call). The assertion only needs to prove the retrieval query differs from the raw follow-up text, which it does.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_router.py -v`
Expected: FAIL — `conversation_id` is not passed through to `generate()`, so the response never carries it as the request intended (first new test may pass trivially since `None` is already the default; the second new test fails because the two calls don't share conversation state without the passthrough).

- [ ] **Step 3: Write minimal implementation**

Replace the full contents of `app/generation/router.py` with:

```python
"""Generation API: grounded answer synthesis over retrieved chunks."""

import logging

from fastapi import APIRouter, HTTPException

from app.generation.schemas import GenerationQuery, GenerationResponse
from app.generation.service import generate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])


@router.post("/query")
def query(query_request: GenerationQuery) -> GenerationResponse:
    """Run retrieval + LLM synthesis and return a grounded, cited answer."""
    try:
        return generate(
            query_request.query,
            query_request.top_k,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
            conversation_id=query_request.conversation_id,
        )
    except Exception as exc:
        logger.exception("Generation query failed")
        raise HTTPException(status_code=503, detail="Generation query failed") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_router.py -v`
Expected: PASS (all existing tests + 2 new ones)

- [ ] **Step 5: Run the full test suite and coverage gate**

Ensure the local Postgres and Redis dev containers are running (`docker compose up -d postgres redis`) if not already, then run:

`uv run pytest --cov=app --cov-fail-under=90`

Expected: all tests pass, coverage gate holds. Also run `uv run mypy app` and `uv run ruff check .` and confirm both are clean — ERP-017's final review found a `mypy --strict` failure that survived every per-task review because no task explicitly ran it; run it now, not just at the very end.

- [ ] **Step 6: Commit**

```bash
git add app/generation/router.py tests/generation/test_router.py
git commit -m "feat: pass conversation_id through generation router for ERP-018"
```

---

## Task 8: Ticket, session log, and current-state updates

**Files:**
- Create: `.ai/tickets/ERP-018.md` (follow `.ai/templates/` ticket template and the shape of `.ai/tickets/ERP-017.md`)
- Create: `.ai/sessions/2026-09-01-conversation-memory.md` (follow `.ai/templates/session.md`)
- Modify: `.ai/memory/current-state.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Read the existing ERP-017 ticket and the most recent session file to match format**

Read `.ai/tickets/ERP-017.md` and `.ai/sessions/2026-08-31-generation.md` for structure/headings to mirror.

- [ ] **Step 2: Write `.ai/tickets/ERP-018.md`**

Follow the ticket template's structure (title, context, scope, acceptance criteria, links to the design spec at `docs/superpowers/specs/2026-09-01-conversation-memory-design.md`); mark it `Done` once Task 7 is committed, listing the feature commits.

- [ ] **Step 3: Write `.ai/sessions/2026-09-01-conversation-memory.md`**

Summarize what was decided (query rewriting via LLM, Postgres-backed storage, `conversation_id` as a stateless/stateful switch with client-supplied get-or-create semantics — including the mid-brainstorm fix that resolved the spec's stateless-vs-always-persist contradiction) and what was built (the new `models.py`/`repository.py`/`rewrite.py` files, the modified `schemas.py`/`prompt.py`/`service.py`/`router.py`, the Alembic migration, the seven feature commits).

- [ ] **Step 4: Update `.ai/memory/current-state.md` in place**

Add a bullet under "What Exists" describing `conversation_id` support on `POST /generation/query` (mirroring the existing ERP-017/014/015/016 bullets' style and level of detail), remove "multi-turn conversation memory" from "What Does Not Exist Yet" (keep streaming, which remains undelivered), and update "Next Planned Work" accordingly (drop conversation memory from any forward-looking mention; a `GET /conversations/{id}` read endpoint is a reasonable new forward-looking item to add, since the spec explicitly named it as deferred-not-abandoned).

- [ ] **Step 5: Commit**

```bash
git add .ai/tickets/ERP-018.md .ai/sessions/2026-09-01-conversation-memory.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-018 with session log and current-state update"
```

---

## Post-Plan: PR

Once all tasks are committed on the feature branch, open a PR against `develop` (following this repo's established `develop`-then-promote-to-`main` flow), titled around "feat: add conversation memory to generation endpoint (ERP-018)", summarizing the new tables/modules and linking the design spec and session log. Do not merge without the user's explicit go-ahead.
