import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_generation_backend(monkeypatch):
    """Stub retrieval and the LLM client so no real Ollama/Postgres call is made."""

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        return []

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    yield


def test_query_returns_no_context_answer_when_retrieval_empty():
    response = client.post("/generation/query", json={"query": "anything"})
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert "don't have enough information" in body["answer"]


def test_query_rejects_empty_query_string():
    response = client.post("/generation/query", json={"query": ""})
    assert response.status_code == 422


def test_query_rejects_top_k_out_of_bounds():
    response = client.post("/generation/query", json={"query": "x", "top_k": 0})
    assert response.status_code == 422


def test_query_returns_answer_with_citations(monkeypatch):
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
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer [1]"
    )

    response = client.post("/generation/query", json={"query": "what is X?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "the answer [1]"
    assert body["citations"] == [
        {
            "chunk_id": "c1",
            "document_id": "doc-1",
            "section_path": ["Intro"],
            "page_start": 1,
            "page_end": 1,
            "source_filename": "doc.pdf",
        }
    ]


def test_query_returns_503_when_llm_backend_unavailable(monkeypatch):
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
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )

    from app.generation.client import OllamaLLMClient

    def _raise_generate(self, system_prompt, user_prompt):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(OllamaLLMClient, "generate", _raise_generate)

    response = client.post("/generation/query", json={"query": "what is X?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Generation query failed"}


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

    # Three canned answers are needed, not two: the first HTTP call consumes one
    # (final synthesis, turn 1 has no history to rewrite); the second HTTP call
    # consumes two (query rewrite, since history now exists, then final synthesis).
    answers = iter(["it has three steps.", "the second step is test.", "here is more detail."])
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


def test_get_conversation_returns_404_for_unknown_id():
    import uuid

    response = client.get(f"/conversations/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_conversation_returns_history_ordered_oldest_first(monkeypatch):
    import uuid

    from app.generation.client import OllamaLLMClient
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
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )
    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    create_response = client.post(
        "/generation/query",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
    )
    assert create_response.status_code == 200

    response = client.get(f"/conversations/{conversation_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == "what is the deployment process?"
    assert body["messages"][1]["content"] == "the answer"


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
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
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
