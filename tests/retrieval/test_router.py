import time

import pytest
from fastapi.testclient import TestClient

from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import get_embedding_settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _stub_embedding_backend(monkeypatch, tmp_path):
    """Stub Ollama and redirect the FAISS index to a temp path.

    Same pattern as the ingestion router's tests — POST /retrieval/query uses production
    defaults with no injected fakes.
    """
    monkeypatch.setenv("EMBEDDING_FAISS_INDEX_PATH", str(tmp_path / "retrieval_router_index.bin"))
    get_embedding_settings.cache_clear()

    def _fake_embed(self, texts):
        dimension = get_embedding_settings().dimension
        return [[0.1] * dimension for _ in texts]

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", _fake_embed)
    try:
        yield
    finally:
        get_embedding_settings.cache_clear()


def test_query_on_empty_index_returns_empty_results():
    response = client.post("/retrieval/query", json={"query": "anything"})
    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_query_rejects_empty_query_string():
    response = client.post("/retrieval/query", json={"query": ""})
    assert response.status_code == 422


def test_query_rejects_top_k_out_of_bounds():
    response = client.post("/retrieval/query", json={"query": "x", "top_k": 0})
    assert response.status_code == 422


def test_query_returns_503_when_embedding_backend_unavailable(monkeypatch):
    def _raise_embed(self, texts):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", _raise_embed)

    response = client.post("/retrieval/query", json={"query": "anything"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Retrieval query failed"}


def test_query_returns_ingested_chunk(simple_text_pdf):
    with open(simple_text_pdf, "rb") as pdf_file:
        upload = client.post(
            "/ingestion/pdf",
            files={"file": ("simple.pdf", pdf_file, "application/pdf")},
        )
    assert upload.status_code == 202
    job_id = upload.json()["job_id"]

    deadline = time.monotonic() + 60.0
    status_body = None
    while time.monotonic() < deadline:
        status_response = client.get(f"/ingestion/jobs/{job_id}")
        status_body = status_response.json()
        if status_body["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status_body is not None
    assert status_body["status"] == "done"

    response = client.post("/retrieval/query", json={"query": "introduction", "top_k": 3})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert results[0]["document_id"] == status_body["result"]["document_id"]
    assert 0 < results[0]["score"] <= 1.0

    reranked_response = client.post(
        "/retrieval/query", json={"query": "introduction", "top_k": 3, "rerank": True}
    )
    assert reranked_response.status_code == 200
    reranked_results = reranked_response.json()["results"]
    assert reranked_results
    assert reranked_results[0]["document_id"] == status_body["result"]["document_id"]

    expanded_response = client.post(
        "/retrieval/query", json={"query": "introduction", "top_k": 3, "expand_sections": True}
    )
    assert expanded_response.status_code == 200
    expanded_results = expanded_response.json()["results"]
    assert expanded_results
    assert expanded_results[0]["document_id"] == status_body["result"]["document_id"]
