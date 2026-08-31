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
