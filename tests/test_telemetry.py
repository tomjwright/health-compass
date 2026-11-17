import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from health_compass.telemetry import JsonFormatter


def make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)


def test_log_line_is_json_without_trace_outside_a_span() -> None:
    line = json.loads(JsonFormatter().format(make_record("hello")))
    assert line["message"] == "hello"
    assert line["level"] == "INFO"
    assert line["trace_id"] is None


def test_log_line_carries_the_current_trace_id() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        line = json.loads(JsonFormatter().format(make_record("inside")))
    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert line["span_id"] == format(span.get_span_context().span_id, "016x")
    assert trace.get_current_span().get_span_context().is_valid is False
