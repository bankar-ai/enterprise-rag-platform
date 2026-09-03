# Streaming Generation (ERP-020) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /generation/query/stream`, an SSE counterpart to the existing `POST /generation/query`, so a client can render the LLM's answer token-by-token instead of waiting for the full completion — with full conversation-memory parity (same `conversation_id` semantics) and without changing the existing sync endpoint at all.

**Architecture:** A new `LLMClient.generate_stream` method (Ollama's `chat(..., stream=True)`) feeds a new `service.generate_stream` generator that mirrors `generate()`'s stateless/stateful branching but yields `(event_name, data_dict)` tuples instead of returning one response. The router wraps that generator in a `StreamingResponse`, formatting each tuple as one SSE frame. Persistence and citation/answer semantics are identical to the sync endpoint; only the delivery mechanism changes.

**Tech Stack:** FastAPI `StreamingResponse` (SSE, `text/event-stream`), `ollama` Python client's existing `stream=True` chat mode. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-streaming-generation-design.md`

## Global Constraints

- Dependency management: `uv add`/`uv remove`/`uv sync`/`uv run` only — never `pip`. No new dependency needed for this feature.
- No `print()` in application code — this module already uses `logging.getLogger(__name__)` conventions elsewhere; follow the same pattern.
- 90% coverage gate applies (`--cov-fail-under=90` in CI) — every new branch needs a covering test.
- Conventional Commits (`feat:`, `test:`, `docs:`) — one logical change per commit.
- `POST /generation/query`, `GET /conversations/{id}`, and every existing test in `tests/generation/` must remain untouched and passing — this plan is purely additive.
- Never hardcode secrets; not applicable here (no new config beyond what `GenerationSettings` already provides).

---

## Task 1: `LLMClient.generate_stream` (Ollama streaming client)

**Files:**
- Modify: `app/generation/client.py`
- Test: `tests/generation/test_client.py`

**Interfaces:**
- Consumes: `GenerationSettings` (existing, `app/generation/config.py`) — `ollama_host`, `model`, `temperature`.
- Produces: `LLMClient.generate_stream(system_prompt: str, user_prompt: str) -> Iterator[str]` (Protocol method), implemented by `OllamaLLMClient.generate_stream`. Later tasks depend on calling `llm_client.generate_stream(SYSTEM_PROMPT, user_prompt)` and iterating the yielded strings.

- [ ] **Step 1: Write the failing test**

Add to `tests/generation/test_client.py`:

```python
def test_generate_stream_calls_ollama_with_stream_true_and_yields_content(monkeypatch):
    class _FakeStreamingOllamaClient:
        def __init__(self, host):
            self.host = host
            self.calls = []

        def chat(self, model, messages, options, think=None, stream=False):
            self.calls.append((model, messages, options, think, stream))
            return iter(
                [
                    ollama.ChatResponse(message=ollama.Message(role="assistant", content="Hello")),
                    ollama.ChatResponse(message=ollama.Message(role="assistant", content=" world")),
                    ollama.ChatResponse(message=ollama.Message(role="assistant", content=None)),
                ]
            )

    fake = _FakeStreamingOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: fake)
    settings = GenerationSettings(
        ollama_host="http://fake:11434", model="test-model", temperature=0.2
    )

    client = OllamaLLMClient(settings)
    chunks = list(client.generate_stream("system text", "user text"))

    assert chunks == ["Hello", " world"]
    assert fake.calls == [
        (
            "test-model",
            [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": "user text"},
            ],
            {"temperature": 0.2},
            False,
            True,
        )
    ]
```

This is a new test function appended to the existing file — the existing `_FakeOllamaClient` class and `test_generate_calls_ollama_with_system_and_user_messages` test are untouched.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_client.py::test_generate_stream_calls_ollama_with_stream_true_and_yields_content -v`
Expected: FAIL with `AttributeError: 'OllamaLLMClient' object has no attribute 'generate_stream'`

- [ ] **Step 3: Implement `generate_stream`**

In `app/generation/client.py`, change the top import and add the method:

```python
from typing import Iterator, Protocol
```

Add to the `LLMClient` Protocol (after the existing `generate` method):

```python
    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Yield the model's answer text in chunks, in order, for the given prompts."""
        ...
```

Add to `OllamaLLMClient` (after the existing `generate` method):

```python
    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Stream `system_prompt`/`user_prompt` to Ollama, yielding response text chunks in order."""
        stream = self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self._temperature},
            think=False,
            stream=True,
        )
        for chunk in stream:
            content = chunk.message.content
            if content:
                yield content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_client.py -v`
Expected: PASS (both the new test and the existing `test_generate_calls_ollama_with_system_and_user_messages`)

- [ ] **Step 5: Type-check and lint**

Run: `uv run mypy app/generation/client.py` and `uv run ruff check app/generation/client.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add app/generation/client.py tests/generation/test_client.py
git commit -m "feat: add LLMClient.generate_stream for streaming Ollama responses (ERP-020)"
```

---

## Task 2: `service.generate_stream`

**Files:**
- Modify: `app/generation/service.py`
- Test: `tests/generation/test_service.py`

**Interfaces:**
- Consumes: `LLMClient.generate_stream` (Task 1), and everything `generate()` already consumes: `retrieval_search`, `build_prompt`, `_citations_for`, `rewrite_query`, `get_session_factory`, `get_recent_messages`, `get_or_create_conversation`, `append_message`, `NO_CONTEXT_ANSWER`.
- Produces: `generate_stream(query: str, top_k: int, rerank: bool = False, expand_sections: bool = False, conversation_id: uuid.UUID | None = None, settings: GenerationSettings | None = None, llm_client: LLMClient | None = None) -> Iterator[tuple[str, dict[str, Any]]]`. Later tasks (router) depend on iterating this and treating each yielded tuple as `(event_name, json_serializable_payload)`; the terminal event is either `("done", {"conversation_id": str | None})` or `("error", {"detail": str})`.

This task builds the generator incrementally, one behavior per test/implement pair, all within `app/generation/service.py`.

- [ ] **Step 1: Write the failing test for the stateless success path**

Add to `tests/generation/test_service.py`:

```python
class _FakeStreamingLLMClient:
    def __init__(self, chunks, rewritten_query=None):
        self._chunks = chunks
        self._rewritten_query = rewritten_query
        self.stream_calls = []
        self.generate_calls = []

    def generate(self, system_prompt, user_prompt):
        self.generate_calls.append((system_prompt, user_prompt))
        return self._rewritten_query or "unused"

    def generate_stream(self, system_prompt, user_prompt):
        self.stream_calls.append((system_prompt, user_prompt))
        yield from self._chunks


def test_generate_stream_stateless_yields_citations_then_tokens_then_done(monkeypatch):
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeStreamingLLMClient(["Hello", " world"])

    events = list(generate_stream("what is X?", top_k=5, llm_client=fake_llm))

    assert events == [
        (
            "citations",
            {
                "citations": [
                    {
                        "chunk_id": "c1",
                        "document_id": "doc-1",
                        "section_path": ["Intro"],
                        "page_start": 1,
                        "page_end": 1,
                        "source_filename": "doc.pdf",
                    },
                    {
                        "chunk_id": "c2",
                        "document_id": "doc-1",
                        "section_path": ["Intro"],
                        "page_start": 1,
                        "page_end": 1,
                        "source_filename": "doc.pdf",
                    },
                ]
            },
        ),
        ("token", {"text": "Hello"}),
        ("token", {"text": " world"}),
        ("done", {"conversation_id": None}),
    ]
```

Update the `import` line at the top of the file from:
```python
from app.generation.service import NO_CONTEXT_ANSWER, generate, get_conversation_history
```
to:
```python
from app.generation.service import NO_CONTEXT_ANSWER, generate, generate_stream, get_conversation_history
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_service.py::test_generate_stream_stateless_yields_citations_then_tokens_then_done -v`
Expected: FAIL with `ImportError: cannot import name 'generate_stream'`

- [ ] **Step 3: Implement the stateless branch**

In `app/generation/service.py`, add these imports at the top (alongside the existing ones):

```python
import logging
from typing import Any, Iterator
```

Add `logger = logging.getLogger(__name__)` after the imports (before `NO_CONTEXT_ANSWER`).

Add the function after `generate`:

```python
def generate_stream(
    query: str,
    top_k: int,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Streaming counterpart to `generate`: yields `(event, data)` tuples instead of returning
    one `GenerationResponse`.

    Event sequence on success: one `("citations", {"citations": [...]})`, zero or more
    `("token", {"text": "..."})` (one per chunk of generated text), then a terminal
    `("done", {"conversation_id": str | None})`. On any failure, yields a terminal
    `("error", {"detail": "..."})` instead of `"done"` -- callers must treat `"error"` as
    the end of the stream, not attempt to resume iteration.

    Shares `generate()`'s stateless/stateful branching, rewrite, and persistence semantics
    exactly (see `generate`'s docstring) -- only the delivery mechanism differs. Persistence
    for a stateful request happens only after the full answer is assembled, immediately
    before the `"done"` event, so a client disconnect (which raises `GeneratorExit` at the
    suspended `yield`) or a mid-generation exception both skip it, leaving conversation
    history exactly as it was before the request.
    """
    settings = settings or get_generation_settings()
    try:
        if conversation_id is None:
            chunks = retrieval_search(query, top_k, rerank=rerank, expand_sections=expand_sections)
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

        chunks = retrieval_search(rewritten_query, top_k, rerank=rerank, expand_sections=expand_sections)
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
            get_or_create_conversation(write_session, conversation_id)
            append_message(write_session, conversation_id, "user", query)
            append_message(write_session, conversation_id, "assistant", answer)
            write_session.commit()

        yield "done", {"conversation_id": str(conversation_id)}
    except Exception:
        logger.exception("Streaming generation failed")
        yield "error", {"detail": "Generation query failed"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/test_service.py::test_generate_stream_stateless_yields_citations_then_tokens_then_done -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for the stateless empty-context short-circuit**

Add to `tests/generation/test_service.py`:

```python
def test_generate_stream_stateless_short_circuits_on_empty_retrieval(monkeypatch):
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeStreamingLLMClient(["should not be used"])

    events = list(generate_stream("what is X?", top_k=5, llm_client=fake_llm))

    assert events == [
        ("citations", {"citations": []}),
        ("token", {"text": NO_CONTEXT_ANSWER}),
        ("done", {"conversation_id": None}),
    ]
    assert fake_llm.stream_calls == []
```

- [ ] **Step 6: Run test to verify it passes (no implementation change expected)**

Run: `uv run pytest tests/generation/test_service.py -k generate_stream -v`
Expected: PASS — this behavior was already implemented in Step 3.

- [ ] **Step 7: Write the failing test for stateful persistence**

Add to `tests/generation/test_service.py`:

```python
def test_generate_stream_with_conversation_id_persists_after_done(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")])
    fake_llm = _FakeStreamingLLMClient(["the ", "answer"])

    events = list(
        generate_stream("what is X?", top_k=5, conversation_id=conversation_id, llm_client=fake_llm)
    )

    assert events[-1] == ("done", {"conversation_id": str(conversation_id)})
    assert fake_llm.generate_calls == []

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == ["what is X?", "the answer"]
```

- [ ] **Step 8: Run test to verify it passes (no implementation change expected)**

Run: `uv run pytest tests/generation/test_service.py -k generate_stream -v`
Expected: PASS — the stateful branch was already implemented in Step 3.

- [ ] **Step 9: Write the failing test for second-turn rewrite**

Add to `tests/generation/test_service.py`:

```python
def test_generate_stream_second_turn_rewrites_query_using_history(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        append_message(session, conversation_id, "user", "what is the deployment process?")
        append_message(session, conversation_id, "assistant", "it has three steps.")
        session.commit()

    captured_retrieval_query = {}

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        captured_retrieval_query["query"] = query
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    fake_llm = _FakeStreamingLLMClient(
        ["the second step is test."],
        rewritten_query="what is the second step in the deployment process?",
    )

    events = list(
        generate_stream(
            "what about the second one?", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
        )
    )

    assert captured_retrieval_query["query"] == "what is the second step in the deployment process?"
    assert len(fake_llm.generate_calls) == 1
    assert events[-1] == ("done", {"conversation_id": str(conversation_id)})
```

- [ ] **Step 10: Run test to verify it passes (no implementation change expected)**

Run: `uv run pytest tests/generation/test_service.py -k generate_stream -v`
Expected: PASS — `rewrite_query` was already wired into the stateful branch in Step 3.

- [ ] **Step 11: Write the failing test for mid-stream failure**

Add to `tests/generation/test_service.py`:

```python
def test_generate_stream_exception_mid_stream_yields_error_and_persists_nothing(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")])

    class _RaisingLLMClient:
        def generate(self, system_prompt, user_prompt):
            raise AssertionError("rewrite should not run on a conversation's first turn")

        def generate_stream(self, system_prompt, user_prompt):
            yield "partial"
            raise RuntimeError("ollama connection dropped")

    events = list(
        generate_stream(
            "what is X?", top_k=5, conversation_id=conversation_id, llm_client=_RaisingLLMClient()
        )
    )

    assert ("token", {"text": "partial"}) in events
    assert events[-1] == ("error", {"detail": "Generation query failed"})

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert messages == []
```

- [ ] **Step 12: Run test to verify it passes (no implementation change expected)**

Run: `uv run pytest tests/generation/test_service.py -k generate_stream -v`
Expected: PASS — the `try/except` wrapping the whole function body was already implemented in Step 3.

- [ ] **Step 13: Run the full test file and coverage check**

Run: `uv run pytest tests/generation/test_service.py -v` then `uv run pytest --cov=app.generation.service --cov-report=term-missing tests/generation/test_service.py`
Expected: all tests PASS; coverage on `app/generation/service.py` at or above 90%.

- [ ] **Step 14: Type-check and lint**

Run: `uv run mypy app/generation/service.py` and `uv run ruff check app/generation/service.py tests/generation/test_service.py`
Expected: both clean

- [ ] **Step 15: Commit**

```bash
git add app/generation/service.py tests/generation/test_service.py
git commit -m "feat: add service.generate_stream with conversation-memory parity (ERP-020)"
```

---

## Task 3: `POST /generation/query/stream` endpoint

**Files:**
- Modify: `app/generation/router.py`
- Test: `tests/generation/test_router.py`

**Interfaces:**
- Consumes: `generate_stream` (Task 2), `GenerationQuery` (existing schema, `app/generation/schemas.py`).
- Produces: `POST /generation/query/stream`, an SSE endpoint on the existing `router` (prefix `/generation`) returning `StreamingResponse` with `media_type="text/event-stream"`. Frame format: `event: <name>\ndata: <json>\n\n` per event yielded by `generate_stream`.

- [ ] **Step 1: Write the failing tests**

Add `import json` as the first line of `tests/generation/test_router.py`, above the existing `import pytest` line.

Then add this helper and these test functions to `tests/generation/test_router.py`:

```python
def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip("\n").split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((event, data))
    return events


def test_query_stream_returns_no_context_sse_when_retrieval_empty():
    from app.generation.service import NO_CONTEXT_ANSWER

    response = client.post("/generation/query/stream", json={"query": "anything"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert events == [
        ("citations", {"citations": []}),
        ("token", {"text": NO_CONTEXT_ANSWER}),
        ("done", {"conversation_id": None}),
    ]


def test_query_stream_returns_citations_tokens_and_done(monkeypatch):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    def _fake_generate_stream(self, system_prompt, user_prompt):
        yield "the "
        yield "answer [1]"

    monkeypatch.setattr(OllamaLLMClient, "generate_stream", _fake_generate_stream)

    response = client.post("/generation/query/stream", json={"query": "what is X?"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0] == (
        "citations",
        {
            "citations": [
                {
                    "chunk_id": "c1",
                    "document_id": "doc-1",
                    "section_path": ["Intro"],
                    "page_start": 1,
                    "page_end": 1,
                    "source_filename": "doc.pdf",
                }
            ]
        },
    )
    assert events[1] == ("token", {"text": "the "})
    assert events[2] == ("token", {"text": "answer [1]"})
    assert events[3] == ("done", {"conversation_id": None})


def test_query_stream_yields_error_event_on_llm_failure(monkeypatch):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    def _raise_generate_stream(self, system_prompt, user_prompt):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover -- unreachable, only makes this a generator function

    monkeypatch.setattr(OllamaLLMClient, "generate_stream", _raise_generate_stream)

    response = client.post("/generation/query/stream", json={"query": "what is X?"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1] == ("error", {"detail": "Generation query failed"})


def test_query_stream_with_conversation_id_continues_across_two_calls(monkeypatch):
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

    stream_answers = iter([["it has ", "three steps."], ["the second step is test."]])
    generate_answers = iter(["the second question rewritten"])

    def _fake_generate_stream(self, system_prompt, user_prompt):
        yield from next(stream_answers)

    def _fake_generate(self, system_prompt, user_prompt):
        return next(generate_answers)

    monkeypatch.setattr(OllamaLLMClient, "generate_stream", _fake_generate_stream)
    monkeypatch.setattr(OllamaLLMClient, "generate", _fake_generate)

    first = client.post(
        "/generation/query/stream",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
    )
    assert first.status_code == 200
    assert _parse_sse(first.text)[-1] == ("done", {"conversation_id": conversation_id})

    second = client.post(
        "/generation/query/stream",
        json={"query": "what about the second one?", "conversation_id": conversation_id},
    )
    assert second.status_code == 200
    assert _parse_sse(second.text)[-1] == ("done", {"conversation_id": conversation_id})

    assert retrieval_queries[0] == "what is the deployment process?"
    assert retrieval_queries[1] == "the second question rewritten"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/generation/test_router.py -k query_stream -v`
Expected: FAIL with `404 Not Found` (route doesn't exist yet)

- [ ] **Step 3: Implement the endpoint**

In `app/generation/router.py`, update the imports:

```python
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.generation.schemas import ConversationHistoryResponse, GenerationQuery, GenerationResponse
from app.generation.service import generate, generate_stream, get_conversation_history
```

Add, after the existing `query` handler and before `conversations_router`'s handler:

```python
def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _event_stream(query_request: GenerationQuery) -> Iterator[str]:
    for event, data in generate_stream(
        query_request.query,
        query_request.top_k,
        rerank=query_request.rerank,
        expand_sections=query_request.expand_sections,
        conversation_id=query_request.conversation_id,
    ):
        yield _format_sse(event, data)


@router.post("/query/stream")
def query_stream(query_request: GenerationQuery) -> StreamingResponse:
    """Run retrieval + LLM synthesis, streaming the answer as Server-Sent Events.

    Unlike `POST /generation/query`, failures surface as a terminal `error` SSE event
    (status stays 200, since headers are already sent once streaming starts) rather than
    an HTTP error status -- see `generate_stream`'s docstring.
    """
    return StreamingResponse(_event_stream(query_request), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/generation/test_router.py -v`
Expected: PASS (new `query_stream` tests and every pre-existing test in the file)

- [ ] **Step 5: Type-check and lint**

Run: `uv run mypy app/generation/router.py` and `uv run ruff check app/generation/router.py tests/generation/test_router.py`
Expected: both clean

- [ ] **Step 6: Run the full suite with coverage**

Run: `uv run pytest --cov=app --cov-report=term-missing`
Expected: all tests PASS, overall coverage at or above 90% (the CI gate)

- [ ] **Step 7: Commit**

```bash
git add app/generation/router.py tests/generation/test_router.py
git commit -m "feat: add POST /generation/query/stream SSE endpoint (ERP-020)"
```

---

## Task 4: Close out ERP-020

**Files:**
- Create: `.ai/tickets/ERP-020.md`
- Create: `.ai/sessions/2026-09-03-streaming-generation.md`
- Modify: `.ai/memory/current-state.md`

**Interfaces:**
- Consumes: nothing code-level — this task records what Tasks 1-3 built, per `CLAUDE.md`'s session/current-state maintenance rule.
- Produces: nothing consumed by other tasks — terminal documentation task.

- [ ] **Step 1: Run the full suite and capture real numbers**

Run: `uv run pytest --cov=app --cov-report=term-missing -v`

Record the actual total test count and the actual overall coverage percentage from this run's output — do not estimate them.

- [ ] **Step 2: Create the ticket file**

Create `.ai/tickets/ERP-020.md`, following `.ai/templates/ticket.md`'s structure (see `.ai/tickets/ERP-019.md` for a filled example):

```markdown
# ERP-020 — Streaming Generation

Status: Done
Depends On: ERP-017, ERP-018

## Description

Adds `POST /generation/query/stream`, an SSE counterpart to `POST /generation/query` (ERP-017) with full conversation-memory parity (ERP-018's `conversation_id` semantics). Closes out the "Streaming Responses" item in `docs/roadmap.md`, named in `current-state.md` as the largest remaining capability gap after ERP-019. Brainstormed as an architectural task (new interface, several open design decisions on transport, citation timing, and persistence-on-disconnect); design spec at `docs/superpowers/specs/2026-09-03-streaming-generation-design.md`.

## Acceptance Criteria

- [x] `app/generation/client.py`'s `LLMClient` Protocol gains `generate_stream(system_prompt, user_prompt) -> Iterator[str]`; `OllamaLLMClient.generate_stream` implements it via `ollama.Client.chat(..., stream=True)`
- [x] `app/generation/service.py` gains `generate_stream(...)`, mirroring `generate()`'s stateless/stateful branching (history load, rewrite, retrieval, empty-context short-circuit) but yielding `(event, data)` tuples: `citations` once, `token` per chunk, then a terminal `done` or `error`
- [x] `POST /generation/query/stream` on the existing `/generation` router, returning `StreamingResponse(media_type="text/event-stream")`
- [x] Citations are emitted before any token event (known from retrieval/build_prompt, before generation starts)
- [x] Persistence (both turns, one transaction) happens only after the full answer is assembled, matching `POST /generation/query`'s all-or-nothing semantics exactly — a client disconnect or mid-generation exception persists nothing
- [x] Mid-stream failures surface as a terminal `error` SSE event (200 status, since headers are already sent) rather than an HTTP error status
- [x] `POST /generation/query` and `GET /conversations/{id}` are byte-for-byte unchanged
- [x] All new/modified modules covered by tests meeting the 90% coverage gate

## Notes

Full suite: <N> tests, <P>% coverage (gate: 90%), mypy clean, ruff clean. <-- fill in with the real numbers from Step 1.

No new dependency — `ollama`'s existing `stream=True` chat mode and FastAPI's built-in `StreamingResponse` cover this.
```

Replace `<N>`/`<P>` with the real values from Step 1's test run before committing.

- [ ] **Step 3: Create the session log**

Create `.ai/sessions/2026-09-03-streaming-generation.md`, following `.ai/templates/session.md`:

```markdown
# Session — Streaming Generation

Date: 2026-09-03
Tickets Touched: ERP-020

## Decisions

New `POST /generation/query/stream` endpoint (not a `stream` flag on the existing endpoint) keeps `POST /generation/query` completely untouched. Citations are sent as the first SSE event, before any answer tokens, since they're known from retrieval/`build_prompt` before generation starts. Streaming supports full conversation-memory parity with the sync endpoint from the start (not deferred). Partial answers are never persisted on disconnect or mid-generation failure — persistence happens only as the last step before the terminal `done` event, so `GeneratorExit` (disconnect) or an exception (failure) both skip it naturally, with no special-case code needed.

## Implementation Summary

- `app/generation/client.py`: `LLMClient.generate_stream` Protocol method; `OllamaLLMClient.generate_stream` via `ollama.Client.chat(..., stream=True)`.
- `app/generation/service.py`: `generate_stream(...)` generator, yielding `("citations", ...)`, `("token", ...)`×n, then `("done", ...)` or `("error", ...)`. Shares `generate()`'s stateless/stateful branching, rewrite, and persistence logic; `generate()` itself is unchanged.
- `app/generation/router.py`: `POST /generation/query/stream`, formatting each yielded tuple as one SSE frame (`event: <name>\ndata: <json>\n\n`) via `StreamingResponse`.
- Design spec: `docs/superpowers/specs/2026-09-03-streaming-generation-design.md`. Implementation plan: `docs/superpowers/plans/2026-09-03-streaming-generation.md`.

## Blockers

None.

## Next Steps

Promote `develop` → `main` once this PR merges (following the PR #6/#11/#15 pattern). Remaining `docs/roadmap.md` gaps after this: DOCX ingestion, PPTX ingestion, Authentication, Evaluation, Observability, and a real deployment story (Dockerfile + CD) beyond local-dev `docker-compose.yml`.
```

- [ ] **Step 4: Update `current-state.md`**

In `.ai/memory/current-state.md`, add a new bullet under "What Exists" (after the ERP-019 bullet), following the exact style of the surrounding bullets — name the ticket, the new endpoint, the key design choices (new endpoint not a flag, citations-first ordering, no-partial-persistence), and note it's merged to `develop` (fill in the actual PR number and merge commit once the PR is opened and merged — see Task 5 below in your own workflow, not part of this plan). Also remove the "Streaming generation responses ... still not built" line from "What Does Not Exist Yet", and remove the corresponding line from "Next Planned Work".

- [ ] **Step 5: Commit**

```bash
git add .ai/tickets/ERP-020.md .ai/sessions/2026-09-03-streaming-generation.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-020 with session log and current-state update"
```

---

## After This Plan

This plan does not open or merge a PR — follow this repository's established pattern (see `.ai/memory/current-state.md`'s PR history): push the branch, open a `develop`-targeted PR, wait for CI to pass, then merge. A separate `develop` → `main` promotion PR follows later, same as PR #6/#11/#15.
