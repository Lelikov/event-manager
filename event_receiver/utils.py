"""Utility functions for event processing."""

import base64
import hashlib
import json
import uuid
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


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
    key_data = f"{event_type}:{booking_id or 'none'}:{json.dumps(data, sort_keys=True, default=str)}"
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
        if len(parts) >= 2:
            return parts[1]

    return None


def decode_getstream_user_id(*, encoded_user_id: str, encryption_key: bytes) -> str:
    """Decode GetStream encrypted user ID to email.

    GetStream user IDs are AES-encrypted emails. This function decrypts them.

    Args:
        encoded_user_id: Base64-encoded encrypted user ID from GetStream
        encryption_key: AES encryption key (32 bytes)

    Returns:
        Decrypted email address

    Raises:
        ValueError: If decryption fails

    """
    # Add base64 padding if needed
    padding_needed = len(encoded_user_id) % 4
    if padding_needed:
        encoded_user_id += "=" * (4 - padding_needed)

    # Decode from base64
    encrypted_data = base64.urlsafe_b64decode(encoded_user_id)

    # Decrypt using AES-CBC
    cipher = Cipher(
        algorithms.AES(encryption_key),
        modes.CBC(b"\x00" * 16),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(encrypted_data) + decryptor.finalize()

    # Remove PKCS7 padding
    unpadder = padding.PKCS7(128).unpadder()
    decoded_data = unpadder.update(padded_data) + unpadder.finalize()

    return decoded_data.decode()
