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
