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

    root = logging.getLogger()
    assert any(
        isinstance(f, TraceIdFilter) for handler in root.handlers for f in handler.filters
    )
