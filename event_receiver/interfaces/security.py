from typing import Any, Protocol


class IAuthorizationJWTVerifier(Protocol):
    def verify_signature(self, *, token: str) -> dict[str, Any]: ...

    def verify(
        self,
        *,
        token: str | None = None,
        claims: dict[str, Any] | None = None,
        event_source: str,
        event_type: str,
    ) -> dict[str, Any]: ...
