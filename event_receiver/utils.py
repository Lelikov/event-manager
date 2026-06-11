"""Utility functions for event processing."""

import base64
import hashlib
import json
import os
import uuid
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_GCM_NONCE_LENGTH = 12
_GCM_TAG_LENGTH = 16


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


def encode_getstream_user_id(*, email: str, encryption_key: bytes) -> str:
    """Encrypt an email into a GetStream user ID using AES-GCM with a random nonce.

    Output format: urlsafe-base64(nonce[12] + ciphertext + tag[16]), unpadded.
    This is the canonical encoder; producers (event-booking) must use the same scheme.
    """
    nonce = os.urandom(_GCM_NONCE_LENGTH)
    ciphertext = AESGCM(encryption_key).encrypt(nonce, email.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).rstrip(b"=").decode()


def decode_getstream_user_id(*, encoded_user_id: str, encryption_key: bytes) -> str:
    """Decode GetStream encrypted user ID to email.

    Tries the authenticated AES-GCM format first (random nonce prepended);
    falls back to the legacy AES-CBC zero-IV format produced by older encoders.

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

    encrypted_data = base64.urlsafe_b64decode(encoded_user_id)

    if len(encrypted_data) > _GCM_NONCE_LENGTH + _GCM_TAG_LENGTH:
        try:
            return _decrypt_gcm(encrypted_data, encryption_key)
        except InvalidTag, ValueError:
            pass

    return _decrypt_legacy_cbc(encrypted_data, encryption_key)


def _decrypt_gcm(encrypted_data: bytes, encryption_key: bytes) -> str:
    nonce = encrypted_data[:_GCM_NONCE_LENGTH]
    ciphertext = encrypted_data[_GCM_NONCE_LENGTH:]
    return AESGCM(encryption_key).decrypt(nonce, ciphertext, None).decode()


def _decrypt_legacy_cbc(encrypted_data: bytes, encryption_key: bytes) -> str:
    """Decrypt the legacy AES-CBC zero-IV format (no integrity; kept for old encoders only)."""
    cipher = Cipher(
        algorithms.AES(encryption_key),
        modes.CBC(b"\x00" * 16),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(encrypted_data) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    decoded_data = unpadder.update(padded_data) + unpadder.finalize()

    return decoded_data.decode()
