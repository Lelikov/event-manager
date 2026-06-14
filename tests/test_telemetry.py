
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from event_receiver.telemetry import add_otel_trace_context, setup_tracing


@pytest.fixture(autouse=True)
def _enable_otel_sdk(monkeypatch) -> None:
    """These tests construct real recording providers; the suite-wide OTEL_SDK_DISABLED
    (set in conftest) would make spans non-recording, so enable the SDK here.
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)


def test_setup_tracing_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    setup_tracing()
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


def test_log_processor_adds_trace_id_for_active_span() -> None:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("op"):
        event_dict = add_otel_trace_context(None, "info", {"event": "hi"})
    assert len(event_dict["trace_id"]) == 32
    assert len(event_dict["span_id"]) == 16


def test_log_processor_skips_when_no_span() -> None:
    event_dict = add_otel_trace_context(None, "info", {"event": "hi"})
    assert "trace_id" not in event_dict
