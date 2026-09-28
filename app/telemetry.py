"""OpenTelemetry setup for Order Tracker: metrics, logs, and traces.

Exporter choice is driven entirely by environment variables, so the same code
serves both Homework 4 stages without changes:

- No `OTEL_EXPORTER_OTLP_ENDPOINT` set: everything prints to stdout, visible via
  `docker compose logs app` (Question 2).
- `OTEL_EXPORTER_OTLP_ENDPOINT` set (e.g. to the OTel Collector): everything is
  exported over OTLP/gRPC instead (Question 3 onward).

Call `setup_telemetry(app)` once, right after the FastAPI app is created.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics
from opentelemetry import trace as trace_api
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "order-tracker")

# The app-level logger used for order-lookup-specific log lines. A separate
# name (not the root logger) keeps this independent of uvicorn's own logging.
logger = logging.getLogger("app.orders")

# Set by setup_telemetry(); the request-metrics middleware in main.py reads this.
request_counter = None


def _otlp_endpoint() -> str | None:
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None


def setup_telemetry(app) -> None:
    global request_counter

    endpoint = _otlp_endpoint()
    resource = Resource.create({"service.name": SERVICE_NAME})

    # --- Traces ---
    tracer_provider = TracerProvider(resource=resource)
    span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True) if endpoint else ConsoleSpanExporter()
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace_api.set_tracer_provider(tracer_provider)

    # --- Metrics ---
    metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True) if endpoint else ConsoleMetricExporter()
    reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    # --- Logs ---
    logger_provider = LoggerProvider(resource=resource)
    log_exporter = OTLPLogExporter(endpoint=endpoint, insecure=True) if endpoint else ConsoleLogExporter()
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # avoid a second copy via uvicorn's root handler

    # Auto-instrument FastAPI: one span per request, with http.route/http.status_code
    # attributes, correlated to whatever log records are emitted inside that request.
    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)

    meter = metrics.get_meter(SERVICE_NAME)
    request_counter = meter.create_counter(
        "http_server_requests_total",
        unit="1",
        description="HTTP requests, by route, method, and status code.",
    )


def log_with_trace(level: int, message: str, *, exc_info: bool = False, **fields) -> None:
    """Log a message, appending the current span's trace_id to the text itself.

    The OTel logging SDK already attaches trace_id/span_id as structured fields
    on the exported log record, but putting the value in the message text too
    means a simple "find the text `trace_id=...`" step - a Grafana Explore
    search, a Loki derived-field regex, grepping `docker compose logs` - always
    works, independent of how any particular backend maps structured metadata.
    """

    span = trace_api.get_current_span()
    trace_id = span.get_span_context().trace_id
    suffix = f" trace_id={trace_id:032x}" if trace_id else ""
    logger.log(level, f"{message}{suffix}", extra=fields, exc_info=exc_info)
