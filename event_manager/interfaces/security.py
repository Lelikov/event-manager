from typing import Any, Protocol


class IFrontendJWTVerifier(Protocol):
    def verify(
        self,
        *,
        token: str,
        payload: dict[str, Any],
        source: str,
        event_type: str,
        require_payload_digest: bool = True,
    ) -> dict[str, Any]: ...


class IBackendSignatureVerifier(Protocol):
    def verify(self, *, body: bytes, signature_header: str) -> bool: ...
