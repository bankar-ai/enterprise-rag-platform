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
