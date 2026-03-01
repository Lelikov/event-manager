import hashlib
import json
from dataclasses import dataclass
from typing import Any

import jwt
import structlog

from event_receiver.errors import UnauthorizedError
from event_receiver.interfaces import IAuthorizationJWTVerifier


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AuthorizationJWTConfig:
    verify_key: str
    algorithm: str
    issuer: str
    audience: str


class AuthorizationJWTVerifier(IAuthorizationJWTVerifier):
    def __init__(self, config: AuthorizationJWTConfig) -> None:
        self._config = config
        logger.debug("AuthorizationJWTVerifier initialized", algorithm=config.algorithm)

    def verify_signature(self, *, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                self._config.verify_key,
                algorithms=[self._config.algorithm],
                audience=self._config.audience,
                issuer=self._config.issuer,
            )
            logger.debug("JWT signature verification succeeded")
        except jwt.PyJWTError as exc:
            logger.warning("JWT signature verification failed")
            raise UnauthorizedError("Invalid JWT signature") from exc
        return claims

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
        logger.debug(
            "Starting JWT claims verification against event fields",
            event_source=event_source,
            event_type=event_type,
        )
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
            logger.warning(
                "JWT source claim mismatch",
                claim_source=source,
                event_source=event_source,
            )
            raise ValueError("JWT source claim does not match request source")
        if _type and _type != event_type:
            logger.warning(
                "JWT type claim mismatch",
                claim_type=_type,
                event_type=event_type,
            )
            raise ValueError("JWT type claim does not match request type")

        excluded_fields = {"source", "type", "exp", "iat", "nbf", "aud", "iss", "sub"}

        filtered_claims = {k: v for k, v in claims.items() if k not in excluded_fields}
        logger.debug("JWT claims verification completed", filtered_claims_count=len(filtered_claims))
        return filtered_claims
