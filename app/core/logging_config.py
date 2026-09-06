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
