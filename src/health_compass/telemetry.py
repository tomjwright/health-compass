"""Tracing and logging setup shared by the API, the pipelines and the agents.

Every log line is one JSON object carrying the current trace and span IDs, so a log
line can be matched to its trace in Jaeger (locally) or Langfuse (hosted).
"""

import json
import logging
import os
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span_context = trace.get_current_span().get_span_context()
        line = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": format(span_context.trace_id, "032x") if span_context.is_valid else None,
            "span_id": format(span_context.span_id, "016x") if span_context.is_valid else None,
        }
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def setup_tracing(service_name: str) -> None:
    """Send spans to the OTLP endpoint in OTEL_EXPORTER_OTLP_ENDPOINT, if one is set.

    Without an endpoint, spans are still created (so logs get trace IDs) but go nowhere.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
