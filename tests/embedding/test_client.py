from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings


class _FakeOllamaClient:
    def __init__(self, host):
        self.host = host
        self.calls = []

    def embed(self, model, input):
        self.calls.append((model, input))
        return {"embeddings": [[0.1, 0.2, 0.3] for _ in input]}


class _FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, model, text):
        return self.store.get((model, text))

    def set(self, model, text, vector):
        self.store[(model, text)] = vector


def test_embed_calls_ollama_with_model_and_texts(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr(
        "app.embedding.client.ollama.Client", lambda host: fake
    )
    settings = EmbeddingSettings(ollama_host="http://fake:11434", model="test-model")

    client = OllamaEmbeddingClient(settings, cache=_FakeCache())
    vectors = client.embed(["a", "b"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.calls == [("test-model", ["a", "b"])]


def test_embed_empty_list_returns_empty_without_calling_ollama(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr(
        "app.embedding.client.ollama.Client", lambda host: fake
    )
    client = OllamaEmbeddingClient(EmbeddingSettings(), cache=_FakeCache())

    assert client.embed([]) == []
    assert fake.calls == []


def test_embed_skips_ollama_entirely_on_full_cache_hit(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
    settings = EmbeddingSettings(model="test-model")
    cache = _FakeCache()
    cache.set("test-model", "a", [9.0, 9.0])

    client = OllamaEmbeddingClient(settings, cache=cache)
    vectors = client.embed(["a"])

    assert vectors == [[9.0, 9.0]]
    assert fake.calls == []


def test_embed_only_sends_cache_misses_to_ollama_and_preserves_order(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
    settings = EmbeddingSettings(model="test-model")
    cache = _FakeCache()
    cache.set("test-model", "cached", [9.0, 9.0])

    client = OllamaEmbeddingClient(settings, cache=cache)
    vectors = client.embed(["cached", "miss"])

    assert vectors == [[9.0, 9.0], [0.1, 0.2, 0.3]]
    assert fake.calls == [("test-model", ["miss"])]


def test_embed_writes_misses_back_to_cache(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
    settings = EmbeddingSettings(model="test-model")
    cache = _FakeCache()

    client = OllamaEmbeddingClient(settings, cache=cache)
    client.embed(["new-text"])

    assert cache.get("test-model", "new-text") == [0.1, 0.2, 0.3]


def test_default_cache_is_redis_backed(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.embedding.client.ollama.Client", lambda host: fake)
    client = OllamaEmbeddingClient(EmbeddingSettings())

    from app.embedding.cache import RedisEmbeddingCache

    assert isinstance(client._cache, RedisEmbeddingCache)
