"""Utility functions for event processing."""

import hashlib
import json
import uuid
from typing import Any


def generate_idempotency_key(
    *,
    event_type: str,
    booking_id: str | None,
    data: dict[str, Any],
) -> str:
    """Generate deterministic idempotency key for event deduplication.

    Args:
        event_type: CloudEvent type
        booking_id: Booking identifier
        data: Event payload

    Returns:
        SHA256 hash as hex string
    """
    key_data = f"{event_type}:{booking_id or 'none'}:{json.dumps(data, sort_keys=True)}"
    return hashlib.sha256(key_data.encode()).hexdigest()


def generate_trace_id() -> str:
    """Generate new trace ID for distributed tracing.

    Returns:
        UUID v4 as string
    """
    return str(uuid.uuid4())


def generate_span_id() -> str:
    """Generate new span ID for distributed tracing.

    Returns:
        UUID v4 as string
    """
    return str(uuid.uuid4())


def extract_trace_id_from_headers(headers: dict[str, str]) -> str | None:
    """Extract trace ID from HTTP headers.

    Checks for common trace header names:
    - X-Trace-Id
    - X-Request-Id
    - traceparent (W3C Trace Context)

    Args:
        headers: HTTP request headers

    Returns:
        Trace ID if found, None otherwise
    """
    # Check X-Trace-Id
    if trace_id := headers.get("X-Trace-Id") or headers.get("x-trace-id"):
        return trace_id

    # Check X-Request-Id
    if request_id := headers.get("X-Request-Id") or headers.get("x-request-id"):
        return request_id

    # Check W3C traceparent (format: 00-{trace_id}-{span_id}-{flags})
    if traceparent := headers.get("traceparent"):
        parts = traceparent.split("-")
        if len(parts) >= 2:  # noqa: PLR2004
            return parts[1]

    return None
