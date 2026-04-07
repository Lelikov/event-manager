import hashlib
import hmac
import re
from typing import TYPE_CHECKING, Any

import structlog
import ujson
from cloudevents.pydantic import from_http
from event_schemas.booking import BookingCreatedPayload
from event_schemas.types import EventType
from pydantic import ValidationError
from stream_chat import StreamChat

from event_receiver.errors import BadRequestError, ConfigurationError, UnauthorizedError
from event_receiver.interfaces.ingest import IIngestController
from event_receiver.utils import extract_trace_id_from_headers


if TYPE_CHECKING:
    from collections.abc import Mapping

    from event_receiver.config import Settings
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

    async def ingest_jitsi(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started Jitsi ingest", body=body, trace_id=trace_id)
        self._authorization_jwt_verifier.verify_signature(token=headers.get("Authorization"))
        logger.debug("JWT signature verification succeeded for Jitsi ingest")

        try:
            incoming = from_http(headers=headers, data=body)
        except Exception as exc:
            logger.warning("Jitsi parsing failed")
            raise BadRequestError("Invalid Jitsi payload or headers") from exc

        logger.debug(
            "Jitsi parsed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            trace_id=trace_id,
        )

        claims = self._authorization_jwt_verifier.verify(
            token=headers.get("Authorization"),
            event_source=incoming.source,
            event_type=incoming.type,
        )
        logger.debug("JWT claims verified and filtered", claims_count=len(claims), trace_id=trace_id)

        await self._publisher.publish(
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            event_time=incoming.time,
            booking_id=claims.get("room"),
            data={**incoming.data, **claims},
            trace_id=trace_id,
        )
        logger.info(
            "Jitsi ingest completed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            trace_id=trace_id,
        )

    async def ingest_booking(self, *, headers: Mapping[str, str], body: bytes) -> None:
        # Extract trace_id from HTTP headers
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started Booking ingest", body=body, trace_id=trace_id)
        if self._settings.booking_api_key != headers.get("Authorization"):
            logger.warning("Booking ingest failed: invalid API key")
            raise UnauthorizedError("Invalid Booking API key")

        try:
            incoming = from_http(headers=headers, data=body)
        except Exception as exc:
            logger.warning("Booking parsing failed")
            raise BadRequestError("Invalid Booking payload or headers") from exc

        logger.debug(
            "Booking parsed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            trace_id=trace_id,
        )

        booking_uid = incoming.data.pop("booking_uid", None)
        if not booking_uid:
            raise BadRequestError("Missing booking_uid in payload")

        if incoming.type == EventType.BOOKING_CREATED:
            payload_dict = self._transform_booking_created_payload(incoming.data)
            try:
                BookingCreatedPayload(**payload_dict)
            except ValidationError as exc:
                logger.exception(
                    "Booking schema validation failed",
                    event_type=incoming.type,
                    validation_errors=exc.errors(),
                )
                raise BadRequestError(f"Invalid booking payload schema: {exc}") from exc
        else:
            payload_dict = incoming.data

        await self._publisher.publish(
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            event_time=incoming.time,
            booking_id=booking_uid,
            data=payload_dict,
            trace_id=trace_id,
        )
        logger.info(
            "Booking ingest completed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            booking_id=booking_uid,
            trace_id=trace_id,
        )

    @staticmethod
    def _transform_booking_created_payload(data: dict[str, Any]) -> dict[str, Any]:
        """Transform users list from booking.created into user/client structure expected by BookingCreatedPayload."""
        users = data.get("users", [])
        organizer = next((u for u in users if u.get("role") == "organizer"), None)
        client = next((u for u in users if u.get("role") == "client"), None)
        if not organizer or not client:
            raise BadRequestError("booking.created requires organizer and client in users")
        return {
            "user": {"email": organizer["email"], "time_zone": organizer["time_zone"]},
            "client": {"email": client["email"], "time_zone": client["time_zone"]},
            "start_time": data["start_time"],
            "end_time": data["end_time"],
        }

    async def ingest_unisender_go(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started UniSender Go ingest", body=body, trace_id=trace_id)

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
                booking_uid = event.get("event_data", {}).get("metadata", {}).pop("booking_uid")
                del event["event_data"]["metadata"]
                await self._publisher.publish(
                    source="unisender-go",
                    event_type=EventType.UNISENDER_STATUS_CREATED,
                    booking_id=booking_uid,
                    data=event,
                    trace_id=trace_id,
                )
        logger.info("UniSender Go ingest completed", trace_id=trace_id)

    async def ingest_getstream(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started Getstream ingest", trace_id=trace_id)
        client = StreamChat(api_key=self._settings.getstream_api_key, api_secret=self._settings.getstream_api_secret)
        if not client.verify_webhook(body, headers["X-SIGNATURE"]):
            logger.warning("Getstream webhook failed: invalid signature")
            raise UnauthorizedError("Invalid Getstream webhook signature")
        data = ujson.loads(body)
        await self._publisher.publish(
            source="getstream",
            event_type=f"getstream.{data.get('type', 'unknown')}",
            booking_id=data.get("channel_id"),
            data=data,
            trace_id=trace_id,
        )
        logger.info("Getstream ingest completed", trace_id=trace_id)

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
