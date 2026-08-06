from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings


class _FakeOllamaClient:
    def __init__(self, host):
        self.host = host
        self.calls = []

    def embed(self, model, input):
        self.calls.append((model, input))
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}


def test_embed_calls_ollama_with_model_and_texts(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr(
        "app.embedding.client.ollama.Client", lambda host: fake
    )
    settings = EmbeddingSettings(ollama_host="http://fake:11434", model="test-model")

    client = OllamaEmbeddingClient(settings)
    vectors = client.embed(["a", "b"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.calls == [("test-model", ["a", "b"])]


def test_embed_empty_list_returns_empty_without_calling_ollama(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr(
        "app.embedding.client.ollama.Client", lambda host: fake
    )
    client = OllamaEmbeddingClient(EmbeddingSettings())

    assert client.embed([]) == []
    assert fake.calls == []
