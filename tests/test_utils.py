"""Tests for GetStream user-id encryption utilities."""

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from event_receiver.utils import decode_getstream_user_id, encode_getstream_user_id


KEY = hashlib.sha256(b"test-getstream-encryption-key").digest()
EMAIL = "participant@example.com"


def _legacy_cbc_encode(email: str, key: bytes) -> str:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(email.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.urlsafe_b64encode(encrypted).rstrip(b"=").decode()


class TestGetstreamUserIdCrypto:
    def test_gcm_round_trip(self) -> None:
        encoded = encode_getstream_user_id(email=EMAIL, encryption_key=KEY)
        assert decode_getstream_user_id(encoded_user_id=encoded, encryption_key=KEY) == EMAIL

    def test_gcm_encoding_is_non_deterministic(self) -> None:
        first = encode_getstream_user_id(email=EMAIL, encryption_key=KEY)
        second = encode_getstream_user_id(email=EMAIL, encryption_key=KEY)
        assert first != second

    def test_tampered_gcm_ciphertext_is_rejected(self) -> None:
        encoded = encode_getstream_user_id(email=EMAIL, encryption_key=KEY)
        raw = bytearray(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        raw[-1] ^= 0xFF
        tampered = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
        with pytest.raises(ValueError):  # noqa: PT011
            decode_getstream_user_id(encoded_user_id=tampered, encryption_key=KEY)

    def test_legacy_cbc_format_still_decodes(self) -> None:
        encoded = _legacy_cbc_encode(EMAIL, KEY)
        assert decode_getstream_user_id(encoded_user_id=encoded, encryption_key=KEY) == EMAIL
