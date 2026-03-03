import hashlib
import hmac
import re
from collections.abc import Mapping

import structlog
import ujson
from cloudevents.pydantic import from_http
from stream_chat import StreamChat

from event_receiver.config import Settings
from event_receiver.errors import BadRequestError, ConfigurationError, UnauthorizedError
from event_receiver.interfaces.ingest import IIngestController
from event_receiver.interfaces.publisher import ICloudEventPublisher
from event_receiver.interfaces.security import IAuthorizationJWTVerifier


logger = structlog.get_logger(__name__)


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
        logger.debug("IngestController initialized")

    async def ingest_cloudevent(self, *, headers: Mapping[str, str], body: bytes) -> None:
        logger.info("Started CloudEvent ingest", body=body)
        self._authorization_jwt_verifier.verify_signature(token=headers.get("Authorization"))
        logger.debug("JWT signature verification succeeded for CloudEvent ingest")

        try:
            incoming = from_http(headers=headers, data=body)
        except Exception as exc:
            logger.warning("CloudEvent parsing failed")
            raise BadRequestError("Invalid CloudEvent payload or headers") from exc

        logger.debug(
            "CloudEvent parsed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
        )

        claims = self._authorization_jwt_verifier.verify(
            token=headers.get("Authorization"),
            event_source=incoming.source,
            event_type=incoming.type,
        )
        logger.debug("JWT claims verified and filtered", claims_count=len(claims))

        await self._publisher.publish(
            source=incoming.source,
            event_type=incoming.type,
            data={**incoming.data, **claims},
            event_id=incoming.id,
            event_time=incoming.time,
        )
        logger.info(
            "CloudEvent ingest completed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
        )

    async def ingest_unisender_go(self, *, headers: Mapping[str, str], body: bytes) -> None:  # noqa: ARG002
        logger.info("Started UniSender Go ingest", body=body)

        if not body:
            logger.warning("UniSender Go ingest failed: empty request body")
            raise BadRequestError("Empty request body")

        try:
            is_valid_signature = self._is_valid_unisender_go_signature(body=body)
        except UnicodeDecodeError as exc:
            logger.warning("UniSender Go ingest failed: body is not valid UTF-8")
            raise BadRequestError("Request body must be valid UTF-8") from exc
        except Exception as exc:
            logger.exception("UniSender Go signature validation failed due to configuration/runtime error")
            raise ConfigurationError("Invalid UniSender Go signature validation configuration") from exc

        if not is_valid_signature:
            logger.warning("UniSender Go ingest failed: invalid auth signature")
            raise UnauthorizedError("Invalid UniSender Go auth signature")

        for event_by_user in ujson.loads(body).get("events_by_user", []):
            for event in event_by_user.get("events", []):
                await self._publisher.publish(
                    source="unisender-go",
                    event_type="unisender.events.v1.transactional.status.create",
                    booking_id=event.get("event_data", {}).get("metadata", {}).get("booking_uid"),
                    data=event,
                )
        logger.info("UniSender Go ingest completed")

    async def ingest_getstream(self, *, headers: Mapping[str, str], body: bytes) -> None:
        logger.info("Started Getstream ingest")
        client = StreamChat(api_key=self._settings.getstream_api_key, api_secret=self._settings.getstream_api_secret)
        if not client.verify_webhook(body, headers["X-SIGNATURE"]):
            logger.warning("Getstream webhook failed: invalid signature")
            raise UnauthorizedError("Invalid Getstream webhook signature")
        data = ujson.loads(body)
        await self._publisher.publish(
            source="getstream",
            event_type=f"getstream.events.v1.{data.get('type', 'unknown')}.create",
            booking_id=data.get("channel_id"),
            data=data,
        )
        logger.info("UniSender Go ingest completed")

    @staticmethod
    def _replace_auth_with_api_key(*, body: str, api_key: str) -> str:
        return re.sub(r'("auth"\s*:\s*")[^"]*(")', rf"\g<1>{api_key}\g<2>", body, count=1)

    def _is_valid_unisender_go_signature(self, *, body: bytes) -> bool:
        body_text = body.decode("utf-8")
        auth_match = re.search(r'"auth"\s*:\s*"([^"]*)"', body_text)
        if not auth_match:
            logger.debug("UniSender Go auth field is missing")
            return False

        incoming_auth = auth_match.group(1)
        body_with_api_key = self._replace_auth_with_api_key(body=body_text, api_key=self._settings.email_api_key)
        expected_signature = hashlib.md5(body_with_api_key.encode("utf-8")).hexdigest()  # noqa: S324
        is_valid = hmac.compare_digest(incoming_auth, expected_signature)
        logger.debug("UniSender Go signature validation result", is_valid=is_valid)
        return is_valid
