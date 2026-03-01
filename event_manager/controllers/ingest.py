import hashlib
import hmac
import re
from collections.abc import Mapping

import ujson
from cloudevents.pydantic import from_http

from event_manager.config import Settings
from event_manager.errors import BadRequestError, ConfigurationError, UnauthorizedError
from event_manager.interfaces.ingest import IIngestController
from event_manager.interfaces.publisher import ICloudEventPublisher
from event_manager.interfaces.security import IAuthorizationJWTVerifier


class IngestController(IIngestController):
    def __init__(
        self,
        *,
        settings: Settings,
        publisher: ICloudEventPublisher,
        authorization_jwt_verifier: IAuthorizationJWTVerifier,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._authorization_jwt_verifier = authorization_jwt_verifier

    async def ingest_cloudevent(self, *, headers: Mapping[str, str], body: bytes) -> None:
        self._authorization_jwt_verifier.verify_signature(token=headers.get("Authorization"))

        try:
            incoming = from_http(headers=headers, data=body)
        except Exception as exc:
            raise BadRequestError("Invalid CloudEvent payload or headers") from exc

        claims = self._authorization_jwt_verifier.verify(
            token=headers.get("Authorization"),
            event_source=incoming.source,
            event_type=incoming.type,
        )

        await self._publisher.publish(
            source=incoming.source,
            event_type=incoming.type,
            data={**incoming.data, **claims},
            event_id=incoming.id,
            event_time=incoming.time,
        )

    async def ingest_unisender_go(self, *, headers: Mapping[str, str], body: bytes) -> None:  # noqa: ARG002
        if not body:
            raise BadRequestError("Empty request body")

        try:
            is_valid_signature = self._is_valid_unisender_go_signature(body=body)
        except UnicodeDecodeError as exc:
            raise BadRequestError("Request body must be valid UTF-8") from exc
        except Exception as exc:
            raise ConfigurationError("Invalid UniSender Go signature validation configuration") from exc
        if not is_valid_signature:
            raise UnauthorizedError("Invalid UniSender Go auth signature")

        await self._publisher.publish(
            source="unisender-go",
            event_type="unisender.events.transactional.status",
            data=ujson.loads(body),
        )

    @staticmethod
    def _replace_auth_with_api_key(*, body: str, api_key: str) -> str:
        return re.sub(r'("auth"\s*:\s*")[^"]*(")', rf"\g<1>{api_key}\g<2>", body, count=1)

    def _is_valid_unisender_go_signature(self, *, body: bytes) -> bool:
        body_text = body.decode("utf-8")
        auth_match = re.search(r'"auth"\s*:\s*"([^"]*)"', body_text)
        if not auth_match:
            return False

        incoming_auth = auth_match.group(1)
        body_with_api_key = self._replace_auth_with_api_key(body=body_text, api_key=self._settings.email_api_key)
        expected_signature = hashlib.md5(body_with_api_key.encode("utf-8")).hexdigest()  # noqa: S324
        return hmac.compare_digest(incoming_auth, expected_signature)
