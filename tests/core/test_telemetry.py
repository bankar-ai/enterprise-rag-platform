from unittest.mock import patch

from fastapi import FastAPI

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
