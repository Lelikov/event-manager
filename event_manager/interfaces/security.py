from typing import Any, Protocol


class IAuthorizationJWTVerifier(Protocol):
    def verify_signature(self, *, token: str) -> dict[str, Any]: ...

    def verify(
        self,
        *,
        token: str,
        event_source: str,
        event_type: str,
    ) -> dict[str, Any]: ...


class IBackendSignatureVerifier(Protocol):
    def verify(self, *, body: bytes, signature_header: str) -> bool: ...
