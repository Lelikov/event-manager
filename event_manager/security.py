import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

import jwt

from event_manager.interfaces import IBackendSignatureVerifier, IFrontendJWTVerifier


@dataclass(frozen=True)
class FrontendJWTConfig:
    verify_key: str
    algorithm: str
    issuer: str
    audience: str


class FrontendJWTVerifier(IFrontendJWTVerifier):
    def __init__(self, config: FrontendJWTConfig) -> None:
        self._config = config

    @staticmethod
    def _payload_digest(payload: dict[str, Any]) -> str:
        canonical_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def verify(
        self,
        *,
        token: str,
        payload: dict[str, Any],
        source: str,
        event_type: str,
        require_payload_digest: bool = True,
    ) -> dict[str, Any]:
        claims: dict[str, Any] = jwt.decode(
            token,
            self._config.verify_key,
            algorithms=[self._config.algorithm],
            audience=self._config.audience,
            issuer=self._config.issuer,
        )

        token_source = claims.get("source")
        token_type = claims.get("type")
        if token_source is not None and token_source != source:
            raise ValueError("JWT source claim does not match request source")
        if token_type is not None and token_type != event_type:
            raise ValueError("JWT type claim does not match request type")

        payload_sha256 = claims.get("payload_sha256")
        if require_payload_digest and payload_sha256 is None:
            raise ValueError("JWT payload_sha256 claim is required")

        if payload_sha256 is not None:
            actual_digest = self._payload_digest(payload)
            if not hmac.compare_digest(str(payload_sha256), actual_digest):
                raise ValueError("JWT payload_sha256 does not match request payload")

        return claims


@dataclass(frozen=True)
class BackendSignatureConfig:
    secret: str
    algorithm: str


class BackendSignatureVerifier(IBackendSignatureVerifier):
    def __init__(self, config: BackendSignatureConfig) -> None:
        self._config = config

    def verify(self, *, body: bytes, signature_header: str) -> bool:
        digest_name = self._config.algorithm.lower()
        if digest_name not in hashlib.algorithms_available:
            raise ValueError(f"Unsupported signature algorithm: {self._config.algorithm}")

        signature_value = signature_header.strip()
        prefix = f"{digest_name}="
        signature_value = signature_value.removeprefix(prefix)

        expected = hmac.new(
            self._config.secret.encode("utf-8"),
            body,
            digestmod=getattr(hashlib, digest_name),
        ).hexdigest()

        return hmac.compare_digest(signature_value, expected)
