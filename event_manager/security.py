import hashlib
import json
from dataclasses import dataclass
from typing import Any

import jwt

from event_manager.errors import UnauthorizedError
from event_manager.interfaces import IAuthorizationJWTVerifier


@dataclass(frozen=True)
class AuthorizationJWTConfig:
    verify_key: str
    algorithm: str
    issuer: str
    audience: str


class AuthorizationJWTVerifier(IAuthorizationJWTVerifier):
    def __init__(self, config: AuthorizationJWTConfig) -> None:
        self._config = config

    def verify_signature(self, *, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(
                token,
                self._config.verify_key,
                algorithms=[self._config.algorithm],
                audience=self._config.audience,
                issuer=self._config.issuer,
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid JWT signature") from exc

    @staticmethod
    def _payload_digest(payload: dict[str, Any]) -> str:
        canonical_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def verify(
        self,
        *,
        token: str,
        event_source: str,
        event_type: str,
    ) -> dict[str, Any]:
        claims: dict[str, Any] = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
                "verify_aud": False,
                "verify_iss": False,
            },
        )

        source = claims.get("source")
        _type = claims.get("type")
        if source and source != event_source:
            raise ValueError("JWT source claim does not match request source")
        if _type and _type != event_type:
            raise ValueError("JWT type claim does not match request type")

        excluded_fields = {"source", "type", "exp", "iat", "nbf", "aud", "iss", "sub"}

        return {k: v for k, v in claims.items() if k not in excluded_fields}
