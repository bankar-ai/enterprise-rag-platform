# Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenTelemetry-based tracing and metrics across the RAG pipeline, exported to Jaeger (traces) and Prometheus/Grafana (metrics), with trace-ID-correlated logs.

**Architecture:** One OTel SDK setup module (`app/core/telemetry.py`) configures global tracer/meter providers and auto-instruments FastAPI/SQLAlchemy/Redis/httpx. Hand-written spans are added at the specific call sites where the pipeline does something auto-instrumentation can't see (embedding, FAISS, BM25, RRF fusion, rerank, section expansion, LLM generation). Metrics are defined next to the concept they measure (cache hit/miss in the cache modules, generation duration in the generation client, job outcomes in the jobs module), not in a central registry file. New `docker-compose` services (Jaeger, Prometheus, Grafana) provide local viewing; none are required for tests, which use OTel's in-memory exporters.

**Tech Stack:** OpenTelemetry Python SDK + contrib instrumentation packages (FastAPI, SQLAlchemy, Redis, httpx), `opentelemetry-exporter-prometheus`, `prometheus-client`, Jaeger (all-in-one, OTLP), Prometheus, Grafana.

**Spec:** `docs/superpowers/specs/2026-09-06-observability-design.md`

## Global Constraints

- All dependencies open-source and free (`docs/architecture.md`) — every package/image listed below is OSS.
- No `print()` in application code; stdlib `logging`, module-level `logger = logging.getLogger(__name__)` (`docs/engineering-guidelines.md`).
- `ruff`, `mypy --strict` (scoped to `app/`), and `pytest-cov --cov-fail-under=90` must all pass (`CLAUDE.md`).
- Never hardcode secrets; config from env vars (`CLAUDE.md`). `OTEL_EXPORTER_OTLP_ENDPOINT` is a public endpoint URL, not a secret, and follows OTel's own standard env-var name rather than this project's usual `<MODULE>_` prefix (an explicit, deliberate exception noted in the spec).
- Observability must never be load-bearing: a missing/unreachable OTLP endpoint must not prevent the app from starting or serving requests (spec's Error Handling & Degradation section).
- Business logic stays in the service layer; API routes only validate/call/return (`docs/architecture.md`) — `/metrics` is a plain mounted ASGI app, not a new business route, so this doesn't apply to it, but no new business logic should land in `app/main.py` beyond wiring.
- New tests only use OTel's own in-memory fixtures (`InMemorySpanExporter`, `InMemoryMetricReader`) — no real Jaeger/Prometheus dependency for CI (spec's Testing section).
- Use `uv add` / `uv sync` for all dependency changes — never `pip install` (`CLAUDE.md`).

---

### Task 1: Dependencies + core telemetry module + log correlation

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `app/core/telemetry.py`
- Create: `app/core/logging_config.py`
- Test: `tests/core/test_telemetry.py`
- Test: `tests/core/test_logging_config.py`
- Create: `tests/core/__init__.py`

**Interfaces:**
- Produces: `app.core.telemetry.configure_telemetry(app: fastapi.FastAPI) -> None`, `app.core.telemetry.get_tracer() -> opentelemetry.trace.Tracer`, `app.core.telemetry.get_meter() -> opentelemetry.metrics.Meter`.
- Produces: `app.core.logging_config.configure_logging() -> None`, `app.core.logging_config.TraceIdFilter` (a `logging.Filter` subclass).
- Consumes: nothing from other tasks (this is the foundation every later task builds on).

- [ ] **Step 1: Add dependencies**

```bash
uv add opentelemetry-sdk opentelemetry-exporter-otlp opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy opentelemetry-instrumentation-redis opentelemetry-instrumentation-httpx opentelemetry-exporter-prometheus prometheus-client
```

- [ ] **Step 2: Write the failing test for log correlation**

Create `tests/core/__init__.py` (empty file).

Create `tests/core/test_logging_config.py`:

```python
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging_config import TraceIdFilter, configure_logging


def test_trace_id_filter_injects_hex_trace_id_when_span_active():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
    filt = TraceIdFilter()

    with tracer.start_as_current_span("span"):
        filt.filter(record)
        span = trace.get_current_span()
        expected = format(span.get_span_context().trace_id, "032x")

    assert record.trace_id == expected


def test_trace_id_filter_uses_placeholder_with_no_active_span():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
    filt = TraceIdFilter()

    filt.filter(record)

    assert record.trace_id == "-"


def test_configure_logging_installs_filter_on_root_logger():
    configure_logging()

    assert any(isinstance(f, TraceIdFilter) for f in logging.getLogger().filters)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/core/test_logging_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.logging_config'`

- [ ] **Step 4: Implement `app/core/logging_config.py`**

```python
"""Trace-ID-correlated structured logging setup.

Injects the active OpenTelemetry span's trace ID into every log record, so a log line can be
pivoted straight to its trace in Jaeger. Never raises: a record with no active span (e.g. at
startup, or in a background thread with no span context) gets the placeholder `"-"`.
"""

import logging

from opentelemetry import trace

_NO_TRACE_PLACEHOLDER = "-"


class TraceIdFilter(logging.Filter):
    """A `logging.Filter` that sets `record.trace_id` from the current OTel span, if any."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set `record.trace_id` and always allow the record through (never filters anything out)."""
        span = trace.get_current_span()
        context = span.get_span_context()
        if context.is_valid:
            record.trace_id = format(context.trace_id, "032x")
        else:
            record.trace_id = _NO_TRACE_PLACEHOLDER
        return True


def configure_logging() -> None:
    """Install `TraceIdFilter` on the root logger and format log records to include the trace ID."""
    handler = logging.StreamHandler()
    handler.addFilter(TraceIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [trace_id=%(trace_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/core/test_logging_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Write the failing test for telemetry setup**

Create `tests/core/test_telemetry.py`:

```python
from unittest.mock import patch

from fastapi import FastAPI
from opentelemetry import trace

from app.core.telemetry import configure_telemetry, get_meter, get_tracer


def test_configure_telemetry_sets_a_global_tracer_provider():
    app = FastAPI()

    configure_telemetry(app)

    tracer = get_tracer()
    with tracer.start_as_current_span("test-span") as span:
        assert span.is_recording()


def test_get_meter_returns_a_working_meter():
    app = FastAPI()
    configure_telemetry(app)

    meter = get_meter()
    counter = meter.create_counter("test_counter")

    counter.add(1)  # must not raise


def test_configure_telemetry_does_not_raise_when_otlp_endpoint_unreachable(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:1")
    app = FastAPI()

    configure_telemetry(app)  # must not raise even though nothing is listening on :1


def test_configure_telemetry_degrades_gracefully_on_setup_error():
    app = FastAPI()
    with patch(
        "app.core.telemetry.FastAPIInstrumentor.instrument_app", side_effect=RuntimeError("boom")
    ):
        configure_telemetry(app)  # must not raise
```

- [ ] **Step 7: Run test to verify it fails**

Run: `uv run pytest tests/core/test_telemetry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.telemetry'`

- [ ] **Step 8: Implement `app/core/telemetry.py`**

```python
"""OpenTelemetry setup: tracer/meter providers, auto-instrumentation, and the `/metrics` registry.

Called once from `app.main` at startup. Never load-bearing: any failure during setup is logged
and swallowed rather than preventing the app from starting or serving requests (observability
must not be able to take the app down).
"""

import logging

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

from app.core.db import get_engine

logger = logging.getLogger(__name__)


def get_tracer() -> Tracer:
    """Return this process's OTel tracer, used by hand-written spans across the codebase."""
    return trace.get_tracer(__name__)


def get_meter() -> Meter:
    """Return this process's OTel meter, used by hand-written metrics across the codebase."""
    return metrics.get_meter(__name__)


def configure_telemetry(app: FastAPI) -> None:
    """Configure global OTel tracer/meter providers and auto-instrument FastAPI/SQLAlchemy/Redis/httpx.

    Reads `OTEL_EXPORTER_OTLP_ENDPOINT` (OTel's own standard env var; defaults to
    `http://localhost:4317`, matching the local `docker-compose` Jaeger service). Any exception
    during setup is logged and swallowed -- the app still starts and serves requests normally,
    just without instrumentation, rather than failing to start because a trace/metrics backend
    isn't reachable yet.
    """
    try:
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(tracer_provider)

        meter_provider = MeterProvider(metric_readers=[PrometheusMetricReader()])
        metrics.set_meter_provider(meter_provider)

        FastAPIInstrumentor.instrument_app(app)
        SQLAlchemyInstrumentor().instrument(engine=get_engine())
        RedisInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.exception("Failed to configure telemetry; continuing without instrumentation")
```

- [ ] **Step 9: Run test to verify it passes**

Run: `uv run pytest tests/core/test_telemetry.py tests/core/test_logging_config.py -v`
Expected: PASS (7 tests)

- [ ] **Step 10: Run mypy on the new modules**

Run: `uv run mypy app/core/telemetry.py app/core/logging_config.py`
Expected: `Success: no issues found`. If any OTel API lacks type stubs and mypy complains, add a narrowly-scoped `# type: ignore[attr-defined]` (or the specific error code mypy reports) on that exact line only, with a one-line comment saying which package lacks stubs -- never a blanket `# type: ignore` on a whole function.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml uv.lock app/core/telemetry.py app/core/logging_config.py tests/core/
git commit -m "feat: add OpenTelemetry setup and trace-ID log correlation"
```

---

### Task 2: Wire telemetry into the app + full-suite regression

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_telemetry_integration.py`

**Interfaces:**
- Consumes: `app.core.telemetry.configure_telemetry` and `app.core.logging_config.configure_logging` from Task 1.
- Produces: nothing new for later tasks (this task proves Task 1's pieces work together in the real app).

- [ ] **Step 1: Write the failing test**

Create `tests/test_telemetry_integration.py`:

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_metrics_endpoint_is_mounted_and_returns_prometheus_text_format():
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_telemetry_integration.py -v`
Expected: FAIL with 404 (no `/metrics` route mounted yet)

- [ ] **Step 3: Wire telemetry into `app/main.py`**

Modify `app/main.py` to:

```python
"""FastAPI application entrypoint."""

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.auth.router import admin_router
from app.auth.router import router as auth_router
from app.core.logging_config import configure_logging
from app.core.telemetry import configure_telemetry
from app.generation.router import conversations_router
from app.generation.router import router as generation_router
from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router

configure_logging()

app = FastAPI(title="Enterprise RAG Platform")
configure_telemetry(app)
app.mount("/metrics", make_asgi_app())
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(ingestion_router)
app.include_router(retrieval_router)
app.include_router(generation_router)
app.include_router(conversations_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_telemetry_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test suite to confirm nothing broke**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest -q --cov=app --cov-fail-under=90`
Expected: all tests pass, coverage still ≥90%. This proves FastAPI/SQLAlchemy/Redis/httpx auto-instrumentation doesn't change any existing behavior (e.g. SQLAlchemy instrumentation must not alter query results, FastAPI instrumentation must not alter response bodies/status codes).

If any existing test fails specifically because of instrumentation noise (e.g. a test asserting on log output that now includes `trace_id=`), fix that test's assertion to match the new log format -- do not weaken instrumentation to avoid touching a test.

- [ ] **Step 6: Run ruff and mypy**

Run: `uv run ruff check app tests && uv run mypy app`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_telemetry_integration.py
git commit -m "feat: wire telemetry and /metrics into the FastAPI app"
```

---

### Task 3: Embedding module instrumentation (cache metric + spans)

**Files:**
- Modify: `app/embedding/cache.py`
- Modify: `app/embedding/client.py`
- Modify: `app/embedding/index.py`
- Test: `tests/embedding/test_telemetry.py`

**Interfaces:**
- Consumes: `app.core.telemetry.get_tracer`, `app.core.telemetry.get_meter` (Task 1).
- Produces: nothing later tasks depend on directly (each instrumented module is independent).

- [ ] **Step 1: Write the failing test**

Create `tests/embedding/test_telemetry.py`:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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
    monkeypatch.setattr("app.embedding.cache._meter", provider.get_meter("test"))
    # Re-create the counter against the patched meter (module-level counters are created at
    # import time against the *global* meter, so tests rebind the module's counter directly).
    from app.embedding import cache as cache_module

    cache_module._cache_requests_counter = cache_module._meter.create_counter(
        "embedding_cache_requests_total"
    )

    settings = type(
        "S", (), {"redis_url": "redis://localhost:6379/9", "cache_ttl_seconds": 60,
                   "redis_socket_timeout_seconds": 2.0}
    )()
    real_cache = RedisEmbeddingCache(settings)
    real_cache.get("model", "text-not-cached-anywhere")

    data = reader.get_metrics_data()
    metric_names = {
        m.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert "embedding_cache_requests_total" in metric_names


def test_faiss_search_produces_a_span():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    import app.embedding.index as index_module

    monkeypatch_tracer = provider.get_tracer("test")
    original = index_module.get_tracer
    index_module.get_tracer = lambda: monkeypatch_tracer
    try:
        index = FaissIndex(":memory-unused:", 2)
        index.add([1, 2], [[0.0, 0.0], [1.0, 1.0]])
        index.search([0.0, 0.0], 1)
    finally:
        index_module.get_tracer = original

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "faiss.search" in span_names


def test_embedding_generate_produces_a_span():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    import app.embedding.client as client_module

    monkeypatch_tracer = provider.get_tracer("test")
    original = client_module.get_tracer
    client_module.get_tracer = lambda: monkeypatch_tracer
    try:
        client = OllamaEmbeddingClient.__new__(OllamaEmbeddingClient)
        client._client = _StubOllama()
        client._model = "test-model"
        client._cache = _StubCache(hit_vector=None)

        client.embed(["hello"])
    finally:
        client_module.get_tracer = original

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "embedding.generate" in span_names
```

Note for the implementer: `FaissIndex.__init__` calls `_load_or_create`, which calls `os.path.exists(":memory-unused:")` -- this returns `False` for a nonexistent path, so it creates an empty index, exactly like the existing tests for `FaissIndex` already do. No filesystem write happens until `.save()` is called, which this test never calls.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/embedding/test_telemetry.py -v`
Expected: FAIL (spans/metrics don't exist yet)

- [ ] **Step 3: Instrument `app/embedding/cache.py`**

Add near the top (after existing imports) and modify `get`:

```python
from app.core.telemetry import get_meter

_meter = get_meter()
_cache_requests_counter = _meter.create_counter(
    "embedding_cache_requests_total", description="Embedding cache lookups by result"
)
```

In `RedisEmbeddingCache.get`, wrap each return path to record the metric. The full modified method:

```python
    def get(self, model: str, text: str) -> list[float] | None:
        """Return the cached vector for `(model, text)`, or `None` on a miss or Redis error."""
        try:
            raw = self._client.get(self._key(model, text))
        except redis.RedisError:
            logger.exception("Redis GET failed; treating as a cache miss")
            _cache_requests_counter.add(1, {"result": "miss"})
            return None
        if raw is None:
            _cache_requests_counter.add(1, {"result": "miss"})
            return None
        try:
            vector: list[float] = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            logger.exception("Failed to deserialize cached embedding vector; treating as a cache miss")
            _cache_requests_counter.add(1, {"result": "miss"})
            return None
        _cache_requests_counter.add(1, {"result": "hit"})
        return vector
```

- [ ] **Step 4: Instrument `app/embedding/index.py`**

Add import: `from app.core.telemetry import get_tracer`

Modify `search`:

```python
    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]:
        """Return up to `k` nearest `(vector_id, distance)` pairs, nearest-first.

        Empty list if the index has no vectors or `k <= 0`. Padding entries FAISS
        returns when the index has fewer than `k` vectors (`vector_id == -1`) are
        dropped.
        """
        with get_tracer().start_as_current_span("faiss.search") as span:
            span.set_attribute("faiss.k", k)
            if self._index.ntotal == 0 or k <= 0:
                span.set_attribute("faiss.hits", 0)
                return []
            query = np.array([vector], dtype="float32")
            distances, ids = self._index.search(query, k)
            hits = [
                (int(vector_id), float(distance))
                for vector_id, distance in zip(ids[0], distances[0], strict=True)
                if vector_id != -1
            ]
            span.set_attribute("faiss.hits", len(hits))
            return hits
```

- [ ] **Step 5: Instrument `app/embedding/client.py`**

Add import: `from app.core.telemetry import get_tracer`

Modify `embed`:

```python
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text in `texts`, in the same order.

        Each text is looked up in the cache first; only cache misses are sent to Ollama,
        in a single batched call, and their results are written back to the cache.
        """
        with get_tracer().start_as_current_span("embedding.generate") as span:
            span.set_attribute("embedding.batch_size", len(texts))
            if not texts:
                return []

            vectors: list[list[float] | None] = [self._cache.get(self._model, text) for text in texts]
            miss_indices = [i for i, vector in enumerate(vectors) if vector is None]
            span.set_attribute("embedding.cache_misses", len(miss_indices))

            if miss_indices:
                miss_texts = [texts[i] for i in miss_indices]
                response = self._client.embed(model=self._model, input=miss_texts)
                new_vectors = list(response["embeddings"])
                for i, vector in zip(miss_indices, new_vectors, strict=True):
                    vectors[i] = vector
                    self._cache.set(self._model, texts[i], vector)

            return [vector for vector in vectors if vector is not None]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/embedding/ -v`
Expected: all pass, including the pre-existing `tests/embedding/` suite (proves the instrumentation didn't change behavior).

- [ ] **Step 7: Run ruff and mypy**

Run: `uv run ruff check app/embedding tests/embedding && uv run mypy app/embedding`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add app/embedding/cache.py app/embedding/client.py app/embedding/index.py tests/embedding/test_telemetry.py
git commit -m "feat: instrument embedding cache/client/index with OTel spans and metrics"
```

---

### Task 4: Retrieval module instrumentation (cache metric + BM25/fusion/rerank/expand spans)

**Files:**
- Modify: `app/retrieval/cache.py`
- Modify: `app/retrieval/service.py`
- Modify: `app/ingestion/repository.py` (the `search_chunks_by_text` function, called by retrieval)
- Test: `tests/retrieval/test_telemetry.py`

**Interfaces:**
- Consumes: `app.core.telemetry.get_tracer`, `app.core.telemetry.get_meter` (Task 1).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Create `tests/retrieval/test_telemetry.py`:

```python
import uuid

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.retrieval import service as service_module


class _StubEmbeddingClient:
    def embed(self, texts):
        return [[0.1, 0.2]]


class _StubFaissIndex:
    def search(self, vector, k):
        return []


class _NoOpCache:
    def get(self, cache_key):
        return None

    def set(self, cache_key, results):
        pass


def test_search_produces_fuse_span_even_with_no_hits(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(service_module, "get_tracer", lambda: provider.get_tracer("test"))

    class _StubSettings:
        dimension = 2
        faiss_index_path = ":unused:"

    service_module.search(
        query="hello",
        top_k=5,
        owner_id=uuid.uuid4(),
        settings=_StubSettings(),
        embedding_client=_StubEmbeddingClient(),
        faiss_index=_StubFaissIndex(),
        cache=_NoOpCache(),
    )

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "retrieval.fuse" in span_names
```

Note for the implementer: this test exercises the real `search()` with no chunks in the (real, test) Postgres DB, so BM25 search legitimately returns zero hits too -- `_reciprocal_rank_fusion` returns `[]`, `search()` returns `[]` early. The `retrieval.fuse` span must still be recorded on this early-return path, not only when there are results.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/retrieval/test_telemetry.py -v`
Expected: FAIL (no `retrieval.fuse` span yet)

- [ ] **Step 3: Instrument `app/ingestion/repository.py`'s `search_chunks_by_text`**

Add import: `from app.core.telemetry import get_tracer`

Wrap the existing body exactly as below -- the SQL and return value are unchanged, only the span and two `set_attribute` calls are new:

```python
def search_chunks_by_text(
    session: Session, query_text: str, k: int, owner_id: uuid.UUID
) -> list[tuple[int, float]]:
    """Full-text search chunk text via Postgres, restricted to `owner_id`'s documents.

    Returns `(vector_id, rank)` pairs, best-first. `[]` for a blank query, `k <= 0`, or no
    matching chunks. Uses `plainto_tsquery` (safe against arbitrary user input, no `tsquery`
    syntax to escape) against the generated `search_vector` column, ranked by `ts_rank`.
    """
    with get_tracer().start_as_current_span("bm25.search") as span:
        span.set_attribute("bm25.k", k)
        if not query_text.strip() or k <= 0:
            span.set_attribute("bm25.hits", 0)
            return []
        tsquery = func.plainto_tsquery("english", query_text)
        rank = func.ts_rank(ChunkRecord.search_vector, tsquery).label("rank")
        rows = session.execute(
            select(ChunkRecord.vector_id, rank)
            .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
            .where(ChunkRecord.search_vector.op("@@")(tsquery), DocumentRecord.owner_id == owner_id)
            .order_by(rank.desc())
            .limit(k)
        ).all()
        result = [(int(vector_id), float(rank_value)) for vector_id, rank_value in rows]
        span.set_attribute("bm25.hits", len(result))
        return result
```

- [ ] **Step 4: Instrument `app/retrieval/cache.py`**

Same pattern as Task 3's `app/embedding/cache.py`. Add:

```python
from app.core.telemetry import get_meter

_meter = get_meter()
_cache_requests_counter = _meter.create_counter(
    "retrieval_cache_requests_total", description="Retrieval cache lookups by result"
)
```

Modify `RedisRetrievalCache.get` to call `_cache_requests_counter.add(1, {"result": "hit"})` or `{"result": "miss"}` on each return path, mirroring exactly how Task 3 changed `RedisEmbeddingCache.get`.

- [ ] **Step 5: Instrument `app/retrieval/service.py`**

Add import: `from app.core.telemetry import get_tracer`

Wrap the fusion, rerank, and section-expansion steps inside `search()`. Modify the body from `fused = _reciprocal_rank_fusion(...)` onward:

```python
        with get_tracer().start_as_current_span("retrieval.fuse") as span:
            fused = _reciprocal_rank_fusion(vector_ranked_ids, bm25_ranked_ids)[:top_k]
            span.set_attribute("retrieval.fused_count", len(fused))
        if not fused:
            cache.set(cache_key, [])
            return []

        chunks_by_vector_id = get_chunks_by_vector_ids(
            session, [vector_id for vector_id, _ in fused], owner_id
        )
        results = []
        for vector_id, score in fused:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
                logger.warning("Dropping fused hit with no matching chunk row: vector_id=%s", vector_id)
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_filename=chunk.source_filename,
                    score=score,
                )
            )

        if rerank:
            with get_tracer().start_as_current_span("retrieval.rerank") as span:
                reranker = reranker or FlashRankReranker(get_reranker_settings())
                results = reranker.rerank(query, results)
                span.set_attribute("retrieval.reranked_count", len(results))

        if expand_sections:
            with get_tracer().start_as_current_span("retrieval.expand_sections") as span:
                results = _expand_sections(session, results)
                span.set_attribute("retrieval.expanded_count", len(results))

        cache.set(cache_key, results)
        return results
```

- [ ] **Step 6: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest tests/retrieval/ tests/ingestion/ -v`
Expected: all pass, including pre-existing suites.

- [ ] **Step 7: Run ruff and mypy**

Run: `uv run ruff check app/retrieval app/ingestion tests/retrieval && uv run mypy app/retrieval app/ingestion`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add app/retrieval/cache.py app/retrieval/service.py app/ingestion/repository.py tests/retrieval/test_telemetry.py
git commit -m "feat: instrument retrieval cache/fusion/rerank/expansion and BM25 search with OTel"
```

---

### Task 5: Generation module instrumentation (LLM span + duration metric)

**Files:**
- Modify: `app/generation/client.py`
- Test: `tests/generation/test_telemetry.py`

**Interfaces:**
- Consumes: `app.core.telemetry.get_tracer`, `app.core.telemetry.get_meter` (Task 1).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Create `tests/generation/test_telemetry.py`:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.generation.client as client_module
from app.generation.client import OllamaLLMClient


class _StubMessage:
    content = "an answer"


class _StubResponse:
    message = _StubMessage()


class _StubOllama:
    def chat(self, **kwargs):
        return _StubResponse()


def test_generate_produces_a_span_and_records_duration(monkeypatch):
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    monkeypatch.setattr(client_module, "get_tracer", lambda: tracer_provider.get_tracer("test"))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    monkeypatch.setattr(client_module, "_duration_histogram", meter_provider.get_meter("test").create_histogram(
        "llm_generation_duration_seconds"
    ))

    client = OllamaLLMClient.__new__(OllamaLLMClient)
    client._client = _StubOllama()
    client._model = "test-model"
    client._temperature = 0.0

    result = client.generate("system", "user")

    assert result == "an answer"
    span_names = [span.name for span in span_exporter.get_finished_spans()]
    assert "llm.generate" in span_names

    data = metric_reader.get_metrics_data()
    metric_names = {
        m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
    }
    assert "llm_generation_duration_seconds" in metric_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/generation/test_telemetry.py -v`
Expected: FAIL (no span/metric yet)

- [ ] **Step 3: Instrument `app/generation/client.py`**

Add imports and module-level histogram:

```python
import time

from app.core.telemetry import get_meter, get_tracer

_duration_histogram = get_meter().create_histogram(
    "llm_generation_duration_seconds", description="Duration of a non-streaming LLM generation call"
)
```

Modify `generate` (leave `generate_stream` unchanged -- streaming duration doesn't map cleanly to one histogram observation and is out of scope for this ticket):

```python
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send `system_prompt`/`user_prompt` to Ollama and return the response text."""
        with get_tracer().start_as_current_span("llm.generate") as span:
            span.set_attribute("llm.model", self._model)
            start = time.monotonic()
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": self._temperature},
                think=False,
            )
            _duration_histogram.record(time.monotonic() - start, {"model": self._model})
            return response.message.content or ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/generation/ -v`
Expected: all pass, including pre-existing `tests/generation/` suite.

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check app/generation tests/generation && uv run mypy app/generation`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add app/generation/client.py tests/generation/test_telemetry.py
git commit -m "feat: instrument LLM generation with an OTel span and duration histogram"
```

---

### Task 6: Ingestion jobs metric

**Files:**
- Modify: `app/ingestion/jobs.py`
- Test: `tests/ingestion/test_telemetry.py`

**Interfaces:**
- Consumes: `app.core.telemetry.get_meter` (Task 1).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_telemetry.py`:

```python
import uuid
from unittest.mock import patch

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import app.ingestion.jobs as jobs_module
from app.ingestion.jobs import create_job, get_job, run_ingestion_job
from app.ingestion.config import IngestionSettings


def test_run_ingestion_job_records_failed_status_metric(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(jobs_module, "_jobs_counter", provider.get_meter("test").create_counter(
        "ingestion_jobs_total"
    ))

    job_id = create_job(uuid.uuid4())
    with patch("app.ingestion.jobs.ingest_pdf", side_effect=ValueError("bad pdf")):
        run_ingestion_job(job_id, "does-not-matter.pdf", "does-not-matter.pdf", IngestionSettings(), uuid.uuid4())

    assert get_job(job_id).status.value == "FAILED"
    data = reader.get_metrics_data()
    points = [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        for point in m.data.data_points
    ]
    assert any(point.attributes.get("status") == "failed" for point in points)
```

Note for the implementer: check `app/ingestion/schemas.py`'s `JobStatus` enum for the exact member names/values before writing the assertion on `get_job(job_id).status.value` -- match what's actually there rather than guessing.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_telemetry.py -v`
Expected: FAIL (no `ingestion_jobs_total` metric yet)

- [ ] **Step 3: Instrument `app/ingestion/jobs.py`**

Add import and module-level counter:

```python
from app.core.telemetry import get_meter

_jobs_counter = get_meter().create_counter(
    "ingestion_jobs_total", description="Completed ingestion jobs by outcome"
)
```

Modify `run_ingestion_job`'s two terminal branches:

```python
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        logger.exception("Ingestion job %s failed for file %r", job_id, filename)
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = str(exc)
        _jobs_counter.add(1, {"status": "failed"})
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
    _jobs_counter.add(1, {"status": "done"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/ -v`
Expected: all pass, including pre-existing `tests/ingestion/` suite.

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check app/ingestion tests/ingestion && uv run mypy app/ingestion`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/jobs.py tests/ingestion/test_telemetry.py
git commit -m "feat: record an ingestion_jobs_total metric by outcome"
```

---

### Task 7: docker-compose Jaeger/Prometheus/Grafana services

**Files:**
- Modify: `docker-compose.yml`
- Create: `observability/prometheus.yml`
- Create: `observability/grafana/provisioning/datasources/datasources.yml`
- Create: `observability/grafana/provisioning/dashboards/dashboards.yml`
- Create: `observability/grafana/provisioning/dashboards/rag-platform.json`

**Interfaces:**
- Consumes: the `/metrics` route from Task 2 (Prometheus scrapes it).
- Produces: nothing consumed by code; this is pure infra/config.

- [ ] **Step 1: Add services to `docker-compose.yml`**

Add these three services alongside the existing `postgres`/`redis`:

```yaml
  jaeger:
    image: jaegertracing/all-in-one:1.76
    ports:
      - "16686:16686"
      - "4317:4317"
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

  prometheus:
    image: prom/prometheus:v3.9.0
    ports:
      - "9090:9090"
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:13.2.0
    ports:
      - "3000:3000"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_AUTH_ANONYMOUS_ENABLED: "true"
```

- [ ] **Step 2: Create `observability/prometheus.yml`**

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: "enterprise-rag-platform"
    static_configs:
      - targets: ["host.docker.internal:8000"]
```

- [ ] **Step 3: Create `observability/grafana/provisioning/datasources/datasources.yml`**

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
  - name: Jaeger
    type: jaeger
    access: proxy
    url: http://jaeger:16686
```

- [ ] **Step 4: Create `observability/grafana/provisioning/dashboards/dashboards.yml`**

```yaml
apiVersion: 1

providers:
  - name: default
    folder: ""
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards
```

- [ ] **Step 5: Create `observability/grafana/provisioning/dashboards/rag-platform.json`**

```json
{
  "title": "Enterprise RAG Platform",
  "timezone": "browser",
  "panels": [
    {
      "id": 1,
      "title": "Request rate",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
      "targets": [{"expr": "sum(rate(http_server_duration_milliseconds_count[5m])) by (http_route)"}]
    },
    {
      "id": 2,
      "title": "p95 request latency",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
      "targets": [{"expr": "histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket[5m])) by (le, http_route))"}]
    },
    {
      "id": 3,
      "title": "Embedding cache hit rate",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
      "targets": [{"expr": "sum(rate(embedding_cache_requests_total{result=\"hit\"}[5m])) / sum(rate(embedding_cache_requests_total[5m]))"}]
    },
    {
      "id": 4,
      "title": "Retrieval cache hit rate",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
      "targets": [{"expr": "sum(rate(retrieval_cache_requests_total{result=\"hit\"}[5m])) / sum(rate(retrieval_cache_requests_total[5m]))"}]
    },
    {
      "id": 5,
      "title": "LLM generation duration (p95)",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 16},
      "targets": [{"expr": "histogram_quantile(0.95, sum(rate(llm_generation_duration_seconds_bucket[5m])) by (le))"}]
    },
    {
      "id": 6,
      "title": "Ingestion jobs by outcome",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 16},
      "targets": [{"expr": "sum(rate(ingestion_jobs_total[5m])) by (status)"}]
    }
  ],
  "schemaVersion": 39,
  "version": 1
}
```

Note for the implementer: the exact metric name FastAPI's OTel instrumentation emits for request duration (`http_server_duration_milliseconds` above) should be verified by starting the app, hitting an endpoint, and checking `curl localhost:8000/metrics` for the real metric name at implementation time -- OTel semantic conventions have changed the exact name across versions, and the dashboard queries must match whatever `opentelemetry-instrumentation-fastapi`'s installed version actually emits. Update the two request-rate/latency panel queries above to match if the real name differs.

- [ ] **Step 6: Validate the compose file**

Run: `docker compose config --quiet`
Expected: no output, exit code 0 (confirms valid YAML and service references).

- [ ] **Step 7: Start the new services and confirm they come up**

Run: `docker compose up -d jaeger prometheus grafana`
Then: `docker compose ps` -- expect all three `Up`/`running`.
Then: open `http://localhost:16686` (Jaeger UI), `http://localhost:9090` (Prometheus), `http://localhost:3000` (Grafana) and confirm each loads. Confirm Prometheus's Targets page (`http://localhost:9090/targets`) shows the `enterprise-rag-platform` job -- it will show as `down` if the app isn't running on the host on port 8000 at the time, which is expected/fine; the config being picked up correctly is what's being verified here, not live scraping.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml observability/
git commit -m "feat: add Jaeger, Prometheus, and Grafana docker-compose services"
```

---

### Task 8: Final verification and documentation

**Files:**
- Modify: `.ai/memory/current-state.md`
- Create: `.ai/tickets/ERP-028.md`
- Create: `.ai/sessions/2026-09-06-observability.md`

**Interfaces:**
- Consumes: nothing (this task verifies and documents everything from Tasks 1-7).
- Produces: nothing (terminal task).

- [ ] **Step 1: Run the full test suite with coverage**

Run: `DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/erp_test" AUTH_JWT_SECRET_KEY="test-only-secret-do-not-use-in-production" uv run pytest -q --cov=app --cov-fail-under=90`
Expected: all tests pass (existing + all new telemetry tests), coverage ≥90%.

- [ ] **Step 2: Run ruff, mypy, and pre-commit across everything changed**

Run: `uv run ruff check app tests && uv run mypy app`
Expected: both clean.

Run: `uv run pre-commit run --files <every file changed across Tasks 1-7>`
Expected: gitleaks and ruff both pass.

- [ ] **Step 3: Write `.ai/tickets/ERP-028.md`**

Follow the format of `.ai/tickets/ERP-027.md` (status Done, Depends On: None, Description referencing this being the "Observability" item from `docs/architecture.md`'s Project Goals, Acceptance Criteria checked off matching this plan's Task 1-7 deliverables, Notes linking to `docs/superpowers/specs/2026-09-06-observability-design.md`).

- [ ] **Step 4: Write `.ai/sessions/2026-09-06-observability.md`**

Follow the format of `.ai/sessions/2026-09-05-admin-user-management.md` (Decisions: OTel as single instrumentation layer, no Collector, degradation-must-not-break-startup; Implementation Summary: list every file touched across Tasks 1-7; Blockers: None or whatever actually came up; Next Steps: Evaluation is the remaining non-deferred roadmap item).

- [ ] **Step 5: Update `.ai/memory/current-state.md`**

Add a new bullet (following the existing style of the ERP-027 bullet) describing what was built, and remove/update the "Next Planned Work" list to drop Observability (it's now done) and note Evaluation as the sole remaining non-deferred item.

- [ ] **Step 6: Commit the documentation**

```bash
git add .ai/tickets/ERP-028.md .ai/sessions/2026-09-06-observability.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-028 (observability)"
```

- [ ] **Step 7: Push and open a PR into `develop`**

```bash
git push -u origin <branch-name>
gh pr create --base develop --title "feat: OpenTelemetry tracing, metrics, and Grafana dashboards (ERP-028)" --body "..."
```

Follow the same PR body structure used for PR #24 (Summary bullets + Test plan checklist referencing the verification in Steps 1-2 above).
