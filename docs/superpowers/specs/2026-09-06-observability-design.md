# Observability Design

Date: 2026-09-06
Status: Approved

## Context

`docs/architecture.md` names Observability as a project goal the platform "must demonstrate," alongside Evaluation. Neither exists yet. Today the platform has structured `logging` (stdlib, module-level loggers, per `docs/engineering-guidelines.md`) but no way to answer "why was this specific request slow," "what did retrieval actually return internally," or "what's the aggregate error/latency picture across the pipeline." This is driven by the general engineering-excellence goal (docs/architecture.md's Project Goal), not a specific incident.

Research into current (2026) practice confirms OpenTelemetry as the standard single instrumentation layer for both traces and metrics in a new Python/FastAPI project, with Prometheus/Jaeger/Grafana as free, open-source backends — consistent with `docs/architecture.md`'s "all dependencies must be open-source and free" constraint. See sources cited in the design conversation (SigNoz, OpenTelemetry Python Contrib docs, openobserve.ai's 2026 Prometheus-vs-OTel comparison).

## Decision

Adopt OpenTelemetry (traces + metrics) as the one instrumentation layer, auto-instrumenting FastAPI/SQLAlchemy/Redis/outbound HTTP, plus hand-written spans around the RAG-specific pipeline stages that auto-instrumentation can't see. Export traces to Jaeger, metrics to Prometheus, both viewed through one Grafana instance. All three are new local `docker-compose` services; none are paid or hosted.

## Scope

**In scope:**
- `app/core/telemetry.py`: OTel SDK setup (tracer provider, meter provider, OTLP exporters), called once from `app/main.py` at startup.
- Auto-instrumentation: FastAPI, SQLAlchemy, Redis, `httpx` (the `ollama` Python client's HTTP transport — confirmed via `import httpx` in `ollama/_client.py`, not `requests`).
- Custom spans: `embedding.generate` (`app/embedding/client.py`), `faiss.search` (`app/embedding/index.py`), `bm25.search` (`app/ingestion/repository.py`), `retrieval.fuse`/`retrieval.rerank`/`retrieval.expand_sections` (`app/retrieval/service.py`), `llm.generate` (`app/generation/client.py`).
- Custom metrics: `embedding_cache_requests_total{result=hit|miss}` (`app/embedding/cache.py`), `retrieval_cache_requests_total{result=hit|miss}` (`app/retrieval/cache.py`), `llm_generation_duration_seconds` (`app/generation/client.py`), `ingestion_jobs_total{status=done|failed}` (`app/ingestion/jobs.py`). HTTP request rate/latency/status come free from FastAPI auto-instrumentation.
- Trace-ID log correlation: a `logging.Filter` that injects the active span's trace ID into every log record, and an updated log format string that includes it.
- New `docker-compose.yml` services: `jaeger` (all-in-one, OTLP gRPC receiver on 4317 + UI on 16686), `prometheus` (scrapes the app's `/metrics`), `grafana` (one provisioned dashboard, Prometheus + Jaeger datasources).
- New dependencies (all free/open-source, added via `uv add`): `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-redis`, `opentelemetry-instrumentation-httpx`, `opentelemetry-exporter-prometheus`, `prometheus-client` (used directly for `make_asgi_app()`; otherwise only a transitive dependency of `opentelemetry-exporter-prometheus`).
- Tests using OTel's in-memory test exporters (`InMemorySpanExporter`, `InMemoryMetricReader`) to assert specific spans/metrics are recorded, without needing a real Jaeger/Prometheus running.
- A degradation test confirming the app still serves requests normally when the OTLP endpoint is unreachable.

**Out of scope (explicitly deferred, do not implement):**
- Evaluation harness (separate roadmap item, separate ticket).
- Full production deployment topology (this is a local/dev-time `docker-compose` addition, not a Kubernetes/cloud observability stack — consistent with "Docker Deployment" being its own separate, not-yet-built roadmap item).
- Alerting (Alertmanager, PagerDuty, etc.) — no on-call story exists for this project.
- Log aggregation service (Loki, ELK) — logs stay on stdout as today; only trace-ID correlation is added, not centralized log storage/search.
- Sampling configuration beyond OTel's default (always-on) — acceptable at this project's scale; revisit if trace volume ever becomes a real cost concern.

## Architecture

```
Request → FastAPI (auto-instrumented: root span)
            ├─ auth dependency (no new spans; already fast)
            ├─ ingestion/retrieval/generation service layer
            │    ├─ embedding.generate span (cache hit/miss recorded as metric + span attribute)
            │    ├─ faiss.search span
            │    ├─ bm25.search span (via SQLAlchemy auto-instrumentation's own DB span, plus a
            │    │    hand-written parent span so "BM25 search" reads as one unit in the trace,
            │    │    not an anonymous SQL statement)
            │    ├─ retrieval.fuse / retrieval.rerank / retrieval.expand_sections spans
            │    └─ llm.generate span (duration also recorded as a histogram metric)
            └─ response

All spans under one root span per request (OTel's default context propagation via
contextvars — no manual span-passing needed within one async request).

Traces  --OTLP/gRPC--> Jaeger (docker-compose service, UI on :16686)
Metrics --Prometheus exporter (/metrics)--> Prometheus (docker-compose, scrapes app) --> Grafana (:3000)
Logs    --stdout, trace_id-tagged--> (pivot to Jaeger by pasting the trace_id into its UI search)
```

No OTel Collector in between — the app exports traces directly to Jaeger's OTLP receiver and exposes `/metrics` directly for Prometheus to scrape. Adding a Collector is a valid future step (fan-out to multiple backends, buffering) but is unnecessary complexity for one backend of each kind — YAGNI.

## Components

### `app/core/telemetry.py` (new)

- `configure_telemetry(app: FastAPI) -> None`: builds the `TracerProvider` (OTLP span exporter, batch processor) and `MeterProvider` (Prometheus metric reader), sets them as the global OTel providers, and calls `FastAPIInstrumentor.instrument_app(app)`, `SQLAlchemyInstrumentor().instrument(engine=get_engine())`, `RedisInstrumentor().instrument()`, `HTTPXClientInstrumentor().instrument()`. Reads `OTEL_EXPORTER_OTLP_ENDPOINT` (standard OTel env var; defaults to `http://localhost:4317` for local dev) — no custom Pydantic settings class needed, this is the one place in the codebase that intentionally uses OTel's own env-var convention instead of the project's `<MODULE>_` prefix, because it's a widely-recognized cross-language standard, not project-specific config.
- `get_tracer() -> Tracer`: thin wrapper around `opentelemetry.trace.get_tracer(__name__)`, used by the hand-written spans below so every call site doesn't repeat the OTel boilerplate.
- The `MeterProvider` is built with a `PrometheusMetricReader` (`opentelemetry.exporter.prometheus`), which registers OTel's metrics into the underlying `prometheus_client` default registry. `app/main.py` mounts `prometheus_client.make_asgi_app()` at `/metrics` (the standard way to expose that registry inside an existing ASGI app, rather than `PrometheusMetricReader`'s alternative of starting a separate HTTP server on its own port) — deliberately unauthenticated (same as every real-world Prometheus target), which is fine since it exposes only aggregate counts/histograms, never per-request data or content. `prometheus-client` becomes an explicit dependency alongside `opentelemetry-exporter-prometheus` (today only a transitive one) since `app/main.py` imports from it directly.

### Logging correlation

`app/core/logging_config.py` (new): a `TraceIdFilter(logging.Filter)` that sets `record.trace_id` from `opentelemetry.trace.get_current_span().get_span_context().trace_id` (formatted as 32 lowercase hex chars, or the literal string `"-"` when there's no active span — e.g. at startup). `configure_logging()` installs this filter on the root logger and sets `logging.basicConfig`'s format to include `%(trace_id)s`. Called once from `app/main.py`, before `configure_telemetry`.

### Custom spans

Each call site wraps its existing logic in `with get_tracer().start_as_current_span("name") as span:` and sets 1-3 attributes that matter for that stage (e.g. `faiss.search` sets `faiss.k` and `faiss.hits`; `llm.generate` sets `llm.model`). No new classes — this is a thin wrapper at the top of each existing function body, not a redesign of any of these modules.

### Custom metrics

One new tiny metrics module isn't needed — each metric is created once (module-level, via `get_tracer_provider()`'s paired `get_meter(__name__).create_counter(...)` / `create_histogram(...)`) at the top of the module that owns that concern (e.g. `embedding_cache_requests_total` lives in `app/embedding/cache.py`, right next to the cache it measures), following this codebase's existing pattern of keeping a concept and its instrumentation together rather than a central "metrics registry" file.

### `docker-compose.yml` additions

```yaml
jaeger:
  image: jaegertracing/all-in-one:1.76
  ports:
    - "16686:16686"  # UI
    - "4317:4317"    # OTLP gRPC receiver
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

Versions confirmed current at design time (2026-09-06): Jaeger 1.76 (jaegertracing.io/docs/1.76), Prometheus v3.9.0 (released 2026-01-06), Grafana 13.2.0 (released 2026-08-18, OSS edition — `grafana/grafana`, not `-enterprise`, to stay within the free/open-source constraint).

New `observability/` directory (repo root, alongside `docker-compose.yml`): `prometheus.yml` (one scrape job pointed at `host.docker.internal:8000/metrics` — the app runs on the host during local dev, not in `docker-compose`, matching how Postgres/Redis are used today), `grafana/provisioning/datasources/*.yml` (Prometheus + Jaeger datasources), `grafana/provisioning/dashboards/*.json` (one dashboard: request rate, p50/p95 latency, error rate, cache hit ratios, LLM generation duration).

## Data Flow Example

A `POST /retrieval/query?rerank=true&expand_sections=true` request produces one trace with this span tree:
```
POST /retrieval/query (FastAPI auto-instrumented root span)
└─ (service layer, no dedicated span — thin passthrough)
   ├─ embedding.generate (attrs: cache_hit=false, batch_size=1)
   ├─ faiss.search (attrs: k=20, hits=18)
   ├─ SELECT ... (SQLAlchemy auto-instrumented, owner filter)
   ├─ bm25.search (attrs: k=20, hits=15)
   ├─ SELECT ... (SQLAlchemy auto-instrumented, hydration)
   ├─ retrieval.fuse (attrs: fused_count=5)
   ├─ retrieval.rerank (attrs: reranked_count=5)
   └─ retrieval.expand_sections (attrs: expanded_count=8)
```
Viewing this trace in Jaeger immediately answers "which stage took the time" — the thing structured logs alone can't show without manually correlating timestamps across log lines.

## Error Handling & Degradation

Observability must never be load-bearing (same principle already established for the Redis caches, ADR-003). OTel's `BatchSpanProcessor` and the Prometheus exporter's pull-based `/metrics` model both already fail closed by design: a `BatchSpanProcessor` exports off a background thread and drops/retries per its own internal policy without raising into request-handling code; a Prometheus reader simply won't have fresh data to expose if metrics couldn't be collected, but nothing about serving `/metrics` can 500 the app itself. `configure_telemetry()` wraps its own construction in `try/except Exception`, logging and returning (auto-instrumentation simply doesn't get installed) rather than the app failing to start if e.g. the OTLP exporter's initial connection attempt is slow/unreachable — a `try/except` at startup, not per-request, since providers are configured once. This will be verified with a test, not assumed (see Testing).

## Testing

- **Existing full suite must keep passing** with instrumentation enabled — proves auto-instrumentation doesn't break any existing behavior (e.g. SQLAlchemy instrumentation wrapping every query must not change query results).
- **Span assertions**: `opentelemetry.sdk.trace.export.InMemorySpanExporter` wired into a test-only `TracerProvider`, used to assert that calling e.g. `search()` produces spans named `embedding.generate`, `faiss.search`, `retrieval.fuse` with the expected attributes.
- **Metric assertions**: `opentelemetry.sdk.metrics.export.InMemoryMetricReader`, used to assert `embedding_cache_requests_total` increments with the right `result` label on a hit vs. a miss.
- **Degradation test**: point `OTEL_EXPORTER_OTLP_ENDPOINT` at an unreachable address, call `configure_telemetry()`, assert it doesn't raise, and assert a request through the app still succeeds normally.
- **Log correlation test**: assert a log record emitted inside an active span carries a `trace_id` attribute matching that span's trace ID (via a `logging.Handler` capturing records in the test).

No new docker-compose services are required for tests — everything above uses OTel's own in-memory test fixtures, matching how this codebase already tests Redis-backed caches against a real local Redis rather than mocks, except here the SDK explicitly ships in-memory fixtures for this exact purpose, so no real Jaeger/Prometheus dependency is needed for CI.

## Future Follow-ups (not in this ticket)

- Evaluation harness (separate roadmap item).
- Full production deployment topology / Kubernetes observability stack.
- Alerting.
- Centralized log aggregation (Loki/ELK).
- An OTel Collector, if a second trace/metric backend is ever needed.
