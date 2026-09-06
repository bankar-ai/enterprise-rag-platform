# Session — Observability

Date: 2026-09-06
Tickets Touched: ERP-028

## Decisions

- Classified as Architectural (brainstorming skill): a new cross-cutting subsystem touching every module, no existing observability flow to extend. Full process followed: clarifying questions, researched approach options, sectioned design, written spec (`docs/superpowers/specs/2026-09-06-observability-design.md`), implementation plan (`docs/superpowers/plans/2026-09-06-observability.md`).
- Researched current (2026) practice before recommending (per `CLAUDE.md`'s research-before-recommending rule): OpenTelemetry is the standard single instrumentation layer for both traces and metrics in a new Python/FastAPI project, with Prometheus/Jaeger/Grafana as free OSS backends — not a separate Prometheus-only instrumentor bolted alongside a second tracing library.
- User chose full three-pillar stack (traces + metrics + Grafana dashboard) over lighter options (metrics-only, or tracing-only), given the project's stated "must demonstrate... Observability" engineering-excellence goal rather than a specific incident.
- No OTel Collector: the app exports traces directly to Jaeger's OTLP receiver and exposes `/metrics` directly for Prometheus to scrape — one backend of each kind doesn't justify a Collector's fan-out/buffering (YAGNI).
- Verified during implementation (not assumed) that `ollama`'s Python client uses `httpx`, not `requests` — the spec's first draft named `opentelemetry-instrumentation-requests`, corrected before it reached the plan.
- Verified during implementation that FastAPI's OTel auto-instrumentation emits `http_server_duration_milliseconds` (not `http_server_duration_seconds` or similar) with an `http_target` label (not `http_route`) — confirmed by starting the app and reading real `/metrics` output, rather than guessing from documentation, before writing the Grafana dashboard queries.
- Pinned Docker image tags (`jaegertracing/all-in-one:1.76.0`, `prom/prometheus:v3.9.0`, `grafana/grafana:13.2.0`) were verified against Docker Hub's actual tag list — the first attempt (`1.76` for Jaeger, missing the `.0`) failed to pull, corrected immediately.
- Observability must never be load-bearing (same principle as the existing Redis caches, ADR-003): `configure_telemetry()` wraps its own setup in `try/except Exception`, logging and continuing rather than failing app startup.

## Implementation Summary

- `app/core/telemetry.py` (new): `configure_telemetry(app)`, `get_tracer()`, `get_meter()`. Auto-instruments FastAPI, SQLAlchemy, Redis, httpx via their respective OTel contrib instrumentors. OTLP span exporter + `PrometheusMetricReader` for metrics.
- `app/core/logging_config.py` (new): `TraceIdFilter` (a `logging.Filter` injecting the active span's trace ID into every log record) + `configure_logging()`.
- `app/main.py`: calls `configure_logging()` and `configure_telemetry(app)` at startup, mounts `prometheus_client.make_asgi_app()` at `/metrics`.
- Custom spans added at: `app/embedding/index.py` (`faiss.search`), `app/embedding/client.py` (`embedding.generate`), `app/ingestion/repository.py` (`bm25.search`), `app/retrieval/service.py` (`retrieval.fuse`, `retrieval.rerank`, `retrieval.expand_sections`), `app/generation/client.py` (`llm.generate`).
- Custom metrics added at: `app/embedding/cache.py` and `app/retrieval/cache.py` (`*_cache_requests_total{result}`), `app/generation/client.py` (`llm_generation_duration_seconds`), `app/ingestion/jobs.py` (`ingestion_jobs_total{status}`).
- `docker-compose.yml` gained `jaeger`, `prometheus`, `grafana` services; new `observability/` directory holds `prometheus.yml` (scrape config) and Grafana's provisioning (`datasources/`, `dashboards/` — one 6-panel dashboard).
- New dependencies (via `uv add`): `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-redis`, `opentelemetry-instrumentation-httpx`, `opentelemetry-exporter-prometheus`, `prometheus-client`.
- Tests: `tests/core/test_telemetry.py`, `tests/core/test_logging_config.py`, `tests/test_telemetry_integration.py`, and a `test_telemetry.py` in each of `tests/embedding/`, `tests/retrieval/`, `tests/generation/`, `tests/ingestion/` — all using OTel's in-memory test fixtures, no real Jaeger/Prometheus needed for CI.
- Verified end-to-end against real local Jaeger/Prometheus/Grafana containers (not just unit tests): `docker compose up -d jaeger prometheus grafana`, confirmed Prometheus's scrape target reports `up`, confirmed Grafana auto-provisioned both datasources and all 6 dashboard panels via its API. Stopped afterward (not required for CI/tests).
- Verified: `ruff` clean, `mypy --strict` clean (50 files), full suite 276 passed at 99.05% coverage, pre-commit (gitleaks + ruff) clean on every file changed on this branch, `docker compose config --quiet` valid.

## Blockers

None.

## Next Steps

Not yet committed for docs (this session's commit follows), not yet pushed/PR'd — that's the remaining step before this ticket is fully closed out, matching the ERP-027 pattern (push, open PR into `develop`, note that `gh pr merge` will need the user to run it directly due to the environment's auto-mode permission classifier). After this, **Evaluation** is the sole remaining non-deferred item from `docs/architecture.md`'s Project Goals / `docs/roadmap.md`.
