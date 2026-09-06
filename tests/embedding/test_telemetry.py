from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.embedding.cache as cache_module
import app.embedding.client as client_module
import app.embedding.index as index_module
from app.embedding.cache import RedisEmbeddingCache
from app.embedding.client import OllamaEmbeddingClient
from app.embedding.index import FaissIndex


class _StubCache:
    def __init__(self, hit_vector=None):
        self._hit_vector = hit_vector

    def get(self, model, text):
        return self._hit_vector

    def set(self, model, text, vector):
        pass


class _StubOllama:
    def embed(self, model, input):
        return {"embeddings": [[0.1, 0.2] for _ in input]}


def test_embedding_cache_records_hit_and_miss_metrics(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(
        cache_module, "_cache_requests_counter", provider.get_meter("test").create_counter(
            "embedding_cache_requests_total"
        )
    )

    settings = type(
        "S",
        (),
        {
            "redis_url": "redis://localhost:6379/9",
            "cache_ttl_seconds": 60,
            "redis_socket_timeout_seconds": 2.0,
        },
    )()
    real_cache = RedisEmbeddingCache(settings)
    real_cache.get("model", "text-not-cached-anywhere")

    data = reader.get_metrics_data()
    metric_names = {
        m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
    }
    assert "embedding_cache_requests_total" in metric_names


def test_faiss_search_produces_a_span(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(index_module, "get_tracer", lambda: provider.get_tracer("test"))

    index = FaissIndex(":memory-unused:", 2)
    index.add([1, 2], [[0.0, 0.0], [1.0, 1.0]])
    index.search([0.0, 0.0], 1)

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "faiss.search" in span_names


def test_embedding_generate_produces_a_span(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(client_module, "get_tracer", lambda: provider.get_tracer("test"))

    client = OllamaEmbeddingClient.__new__(OllamaEmbeddingClient)
    client._client = _StubOllama()
    client._model = "test-model"
    client._cache = _StubCache(hit_vector=None)

    client.embed(["hello"])

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "embedding.generate" in span_names
