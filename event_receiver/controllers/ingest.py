import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
import ujson
from cloudevents.v1.pydantic.v2.conversion import from_http
from event_schemas.booking import BookingCreatedPayload
from event_schemas.types import EventType
from opentelemetry import trace
from pydantic import ValidationError

from event_receiver.calcom import CALCOM_SIGNATURE_HEADER, transform_calcom_webhook
from event_receiver.errors import BadRequestError, ConfigurationError, UnauthorizedError
from event_receiver.interfaces.ingest import IIngestController
from event_receiver.utils import extract_trace_id_from_headers


if TYPE_CHECKING:
    from collections.abc import Mapping

    from event_receiver.config import Settings
    from event_receiver.interfaces.publisher import ICloudEventPublisher
    from event_receiver.interfaces.security import IAuthorizationJWTVerifier


logger = structlog.get_logger(__name__)

_tracer = trace.get_tracer(__name__)


def _user_info(user: dict[str, Any]) -> dict[str, Any]:
    """Build a UserInfo-shaped dict ({email, time_zone?}) from a participant entry."""
    info: dict[str, Any] = {"email": user["email"]}
    if user.get("time_zone"):
        info["time_zone"] = user["time_zone"]
    return info


def _participant_entry(user: dict[str, Any]) -> dict[str, Any]:
    """Build a BookingParticipant-shaped dict; guests are normalized to role 'client'."""
    role = user.get("role")
    entry: dict[str, Any] = {"email": user["email"], "role": "client" if role == "guest" else role}
    if user.get("time_zone"):
        entry["time_zone"] = user["time_zone"]
    if user.get("locale"):
        entry["locale"] = user["locale"]
    return entry


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

        logger.info("Started Jitsi ingest")
        token = headers.get("Authorization")
        if not token:
            raise UnauthorizedError("Missing Authorization header")
        decoded_claims = self._authorization_jwt_verifier.verify_signature(token=token)
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
        )

        claims = self._authorization_jwt_verifier.verify(
            claims=decoded_claims,
            event_source=incoming.source,
            event_type=incoming.type,
        )
        logger.debug("JWT claims verified and filtered", claims_count=len(claims))

        if not isinstance(incoming.data, dict):
            raise BadRequestError("Event data must be a JSON object")

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
        )

    async def ingest_booking(self, *, headers: Mapping[str, str], body: bytes) -> None:
        # Extract trace_id from HTTP headers
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started Booking ingest")
        if not hmac.compare_digest(self._settings.booking_api_key, headers.get("Authorization", "")):
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
        )

        if not isinstance(incoming.data, dict):
            raise BadRequestError("Event data must be a JSON object")
        data = dict(incoming.data)
        booking_uid = data.pop("booking_uid", None)
        if not booking_uid:
            raise BadRequestError("Missing booking_uid in payload")

        payload_dict = data
        if incoming.type == EventType.BOOKING_CREATED:
            payload_dict = self._validated_booking_created(data)

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
        )

    def _validated_booking_created(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform a users-list booking.created payload and validate it against the canonical model."""
        payload_dict = self._transform_booking_created_payload(data)
        try:
            BookingCreatedPayload(**payload_dict)
        except ValidationError as exc:
            logger.warning(
                "Booking schema validation failed",
                validation_errors=exc.errors(),
            )
            raise BadRequestError(f"Invalid booking payload schema: {exc}") from exc
        return payload_dict

    async def ingest_calcom(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))
        logger.info("Started cal.com ingest")

        with _tracer.start_as_current_span("receiver.validate_webhook") as span:
            span.set_attribute("source", "calcom")
            signature = headers.get(CALCOM_SIGNATURE_HEADER) or headers.get("X-Cal-Signature-256")
            if signature is None:
                raise UnauthorizedError("Missing X-Cal-Signature-256 header")
            if not self._is_valid_calcom_signature(body=body, signature=signature):
                logger.warning("cal.com webhook failed: invalid signature")
                raise UnauthorizedError("Invalid cal.com webhook signature")

        webhook = self._parse_json_body(body)
        event = transform_calcom_webhook(webhook)

        data = event.data
        if event.event_type == EventType.BOOKING_CREATED:
            data = self._validated_booking_created(data)

        created_at = webhook.get("createdAt")
        await self._publisher.publish(
            source=event.source,
            event_type=event.event_type,
            event_time=created_at if isinstance(created_at, str) else None,
            booking_id=event.booking_uid,
            data=data,
            trace_id=trace_id,
        )
        logger.info(
            "cal.com ingest completed",
            trigger_event=webhook.get("triggerEvent"),
            event_type=str(event.event_type),
            booking_id=event.booking_uid,
        )

    def _is_valid_calcom_signature(self, *, body: bytes, signature: str) -> bool:
        """Verify a cal.com webhook signature: HMAC-SHA256 hex digest of the raw body with the webhook secret."""
        expected = hmac.new(self._settings.calcom_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _transform_booking_created_payload(data: dict[str, Any]) -> dict[str, Any]:
        """Transform users list from booking.created into user/client structure expected by BookingCreatedPayload.

        The full participant list (organizer + ALL clients/guests) is preserved under ``users``
        so normalization downstream does not silently drop extra attendees; ``user``/``client``
        keep the canonical primary pair. Guests are normalized to role ``client``.
        """
        users = data.get("users", [])
        if not isinstance(users, list) or not all(isinstance(user, dict) for user in users):
            raise BadRequestError("booking.created requires a users list of objects")
        organizer = next((u for u in users if u.get("role") == "organizer"), None)
        clients = [u for u in users if u.get("role") in ("client", "guest")]
        if not organizer or not clients:
            raise BadRequestError("booking.created requires organizer and at least one client in users")
        try:
            result: dict[str, Any] = {
                "user": _user_info(organizer),
                "client": _user_info(clients[0]),
                "start_time": data["start_time"],
                "end_time": data["end_time"],
                "users": [
                    _participant_entry(user)
                    for user in users
                    if user.get("email") and user.get("role") in ("organizer", "client", "guest")
                ],
            }
        except KeyError as exc:
            raise BadRequestError(f"booking.created payload missing required field: {exc}") from exc
        if data.get("volunteer_id"):
            result["volunteer_id"] = data["volunteer_id"]
        if data.get("client_id"):
            result["client_id"] = data["client_id"]
        return result

    async def ingest_unisender_go(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started UniSender Go ingest")

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

        for event_by_user in self._parse_json_body(body).get("events_by_user", []):
            for event in event_by_user.get("events", []):
                event_data = dict(event.get("event_data", {}))
                metadata = dict(event_data.get("metadata", {}))
                booking_uid = metadata.pop("booking_uid", None)
                event_data["metadata"] = metadata
                event_copy = {**event, "event_data": event_data}
                await self._publisher.publish(
                    source="unisender-go",
                    event_type=EventType.UNISENDER_STATUS_CREATED,
                    event_id=str(uuid.uuid4()),
                    event_time=datetime.now(UTC),
                    booking_id=booking_uid,
                    data=event_copy,
                    trace_id=trace_id,
                )
        logger.info("UniSender Go ingest completed")

    async def ingest_getstream(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))

        logger.info("Started Getstream ingest")
        signature = headers.get("X-SIGNATURE")
        if signature is None:
            raise UnauthorizedError("Missing X-SIGNATURE header")
        if not self._is_valid_getstream_signature(body=body, signature=signature):
            logger.warning("Getstream webhook failed: invalid signature")
            raise UnauthorizedError("Invalid Getstream webhook signature")
        data = self._parse_json_body(body)
        await self._publisher.publish(
            source="getstream",
            event_type=f"getstream.{data.get('type', 'unknown')}",
            booking_id=data.get("channel_id"),
            data=data,
            trace_id=trace_id,
        )
        logger.info("Getstream ingest completed")

    async def ingest_admin(self, *, headers: Mapping[str, str], body: bytes) -> None:
        trace_id = extract_trace_id_from_headers(dict(headers))
        logger.info("Started Admin ingest")

        token = self._bearer_token(headers)
        if not hmac.compare_digest(self._settings.admin_api_key, token):
            logger.warning("Admin ingest failed: missing or invalid Bearer API key")
            raise UnauthorizedError("Invalid Admin API key")

        try:
            incoming = from_http(headers=headers, data=body)
        except Exception as exc:
            logger.warning("Admin event parsing failed")
            raise BadRequestError("Invalid Admin event payload or headers") from exc

        data = dict(incoming.data) if isinstance(incoming.data, dict) else {}
        booking_id = data.get("booking_uid") or None

        await self._publisher.publish(
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
            event_time=incoming.time,
            booking_id=booking_id,
            data=data,
            trace_id=trace_id,
        )
        logger.info(
            "Admin ingest completed",
            source=incoming.source,
            event_type=incoming.type,
            event_id=incoming.id,
        )

    def _is_valid_getstream_signature(self, *, body: bytes, signature: str) -> bool:
        """Verify a GetStream webhook signature: HMAC-SHA256 hex digest of the raw body with the API secret."""
        expected = hmac.new(self._settings.getstream_api_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _bearer_token(headers: Mapping[str, str]) -> str:
        """Extract the token from ``Authorization: Bearer <token>``.

        Malformed headers (missing, raw key without scheme, wrong scheme) yield ""
        so the constant-time comparison against the real key always fails.
        """
        authorization = headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return ""
        return token.strip()

    @staticmethod
    def _parse_json_body(body: bytes) -> dict[str, Any]:
        """Parse a raw request body as a JSON object, mapping parse failures to 400."""
        try:
            data = ujson.loads(body)
        except ValueError as exc:
            raise BadRequestError("Request body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise BadRequestError("Request body must be a JSON object")
        return data

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
