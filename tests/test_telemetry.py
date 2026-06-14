import os

import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from event_receiver.telemetry import add_otel_trace_context, setup_tracing


def test_setup_tracing_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    setup_tracing()
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


def test_log_processor_adds_trace_id_for_active_span():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("op"):
        event_dict = add_otel_trace_context(None, "info", {"event": "hi"})
    assert len(event_dict["trace_id"]) == 32
    assert len(event_dict["span_id"]) == 16


def test_log_processor_skips_when_no_span():
    event_dict = add_otel_trace_context(None, "info", {"event": "hi"})
    assert "trace_id" not in event_dict
