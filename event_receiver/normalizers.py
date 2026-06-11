"""Event payload normalizers.

Normalize event payloads from different sources into a standard structure
that event-saver can easily consume without complex extraction logic.
"""

import binascii
from collections.abc import Callable
from typing import Any

import structlog
from event_schemas import (
    BookingCreatedPayload,
    BookingRejectedPayload,
    BookingReminderSentPayload,
    GetStreamEventPayload,
    JitsiEventPayload,
    NotificationCommandPayload,
    UniSenderStatusPayload,
)
from event_schemas.types import EventType
from pydantic import ValidationError


logger = structlog.get_logger(__name__)


def normalize_event_payload(
    event_type: EventType | None,
    payload: dict[str, Any],
    *,
    getstream_decoder: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Normalize event payload to standard structure.

    Args:
        event_type: Event type enum (None for types unknown to the enum)
        payload: Original event payload (dict)
        getstream_decoder: Optional decoder for GetStream encrypted user IDs

    Returns:
        Dict with "original" and "normalized" keys.
        "normalized" contains only "participants" if any could be extracted.
        If normalization fails, normalized participants list is empty.

    """
    if event_type is None:
        return {"original": payload, "normalized": {"participants": []}}

    try:
        participants = _extract_participants(event_type, payload, getstream_decoder=getstream_decoder)
    except (ValidationError, KeyError, ValueError) as e:
        logger.warning("Normalizer error", event_type=event_type, error=repr(e))
        participants = []

    return {"original": payload, "normalized": {"participants": participants}}


def _extract_participants(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    getstream_decoder: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Extract participants from payload based on event type."""
    if event_type.value.startswith("getstream."):
        return _participants_from_getstream_event(payload, getstream_decoder=getstream_decoder)
    if event_type.value.startswith("jitsi."):
        return _participants_from_jitsi_event(payload)

    extractor = _PARTICIPANT_EXTRACTORS.get(event_type)
    if extractor is None:
        return []
    return extractor(payload)


def _participants_from_users_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract participants from a generic 'users' list in the payload."""
    return [
        {k: v for k, v in user.items() if k in ("email", "role", "time_zone", "locale") and v is not None}
        for user in payload.get("users", [])
        if user.get("email")
    ]


def _participants_from_recipient(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the single recipient from meeting.url_* payloads ({email, recipient_role})."""
    email = payload.get("email")
    if not email or not isinstance(email, str):
        return []
    role = payload.get("recipient_role")
    return [{"email": email, "role": role if isinstance(role, str) else None}]


def _participants_from_notification_command(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract participants from notification.send_requested recipients ({email, role})."""
    validated = NotificationCommandPayload(**payload)
    participants: list[dict[str, Any]] = []
    for recipient in validated.recipients:
        participant: dict[str, Any] = {"email": recipient.email, "role": recipient.role.value}
        if recipient.locale:
            participant["locale"] = recipient.locale
        participants.append(participant)
    return participants


def _participants_from_booking_rejected(payload: dict[str, Any]) -> list[dict[str, Any]]:
    validated = BookingRejectedPayload(**payload)
    return [{"email": validated.client_email, "role": "client"}]


def _participants_from_booking_created(payload: dict[str, Any]) -> list[dict[str, Any]]:
    validated = BookingCreatedPayload(**payload)

    users = payload.get("users")
    if isinstance(users, list) and users:
        participants = _booking_created_participants_from_users(users, validated)
        if participants:
            return participants

    organizer: dict[str, Any] = {
        "email": validated.user.email,
        "role": "organizer",
    }
    if validated.volunteer_id:
        organizer["user_id"] = validated.volunteer_id
    client: dict[str, Any] = {
        "email": validated.client.email,
        "role": "client",
    }
    if validated.client_id:
        client["user_id"] = validated.client_id
    return [organizer, client]


def _booking_created_participants_from_users(
    users: list[Any],
    validated: BookingCreatedPayload,
) -> list[dict[str, Any]]:
    """Emit ALL booking.created participants (organizer + every client/guest), not just the primary pair."""
    participants: list[dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict) or not user.get("email"):
            continue
        role = user.get("role")
        role = "client" if role == "guest" else role
        participant: dict[str, Any] = {"email": user["email"], "role": role}
        if user.get("time_zone"):
            participant["time_zone"] = user["time_zone"]
        if user.get("locale"):
            participant["locale"] = user["locale"]
        if role == "organizer" and validated.volunteer_id and user["email"] == validated.user.email:
            participant["user_id"] = validated.volunteer_id
        if role == "client" and validated.client_id and user["email"] == validated.client.email:
            participant["user_id"] = validated.client_id
        participants.append(participant)
    return participants


def _participants_from_booking_reminder_sent(payload: dict[str, Any]) -> list[dict[str, Any]]:
    validated = BookingReminderSentPayload(**payload)
    return [{"email": validated.email}]


def _participants_from_unisender_status(payload: dict[str, Any]) -> list[dict[str, Any]]:
    validated = UniSenderStatusPayload(**payload)
    email = validated.event_data.get("email")
    if not email or not isinstance(email, str):
        return []
    return [{"email": email, "role": validated.event_data.get("metadata", {}).get("role")}]


def _participants_from_getstream_event(
    payload: dict[str, Any],
    *,
    getstream_decoder: Callable[[str], str],
) -> list[dict[str, Any]]:
    validated = GetStreamEventPayload(**payload)

    user_id = None
    if validated.user and isinstance(validated.user, dict):
        user_id = validated.user.get("id")

    if not user_id or not isinstance(user_id, str):
        return []

    try:
        email = getstream_decoder(user_id)
    except ValueError, UnicodeDecodeError, binascii.Error:
        return []

    getstream_role = next((user for user in validated.members if user["user_id"] == user_id), {}).get("role")

    role = "organizer" if getstream_role == "owner" else "client"

    return [{"email": email, "role": role}]


def _participants_from_jitsi_event(payload: dict[str, Any]) -> list[dict[str, Any]]:
    JitsiEventPayload(**payload)

    context = payload.get("context")
    if not isinstance(context, dict):
        return []

    user = context.get("user")
    if not isinstance(user, dict):
        return []

    email = user.get("email")
    if not email or not isinstance(email, str):
        return []

    role = user.get("role")
    return [{"email": email, "role": role if isinstance(role, str) else None}]


_PARTICIPANT_EXTRACTORS: dict[EventType, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    EventType.BOOKING_CREATED: _participants_from_booking_created,
    EventType.BOOKING_CANCELLED: _participants_from_users_list,
    EventType.BOOKING_REASSIGNED: _participants_from_users_list,
    EventType.BOOKING_RESCHEDULED: _participants_from_users_list,
    EventType.BOOKING_REMINDER_SENT: _participants_from_booking_reminder_sent,
    EventType.BOOKING_REJECTED: _participants_from_booking_rejected,
    EventType.MEETING_URL_CREATED: _participants_from_recipient,
    EventType.MEETING_URL_DELETED: _participants_from_recipient,
    EventType.NOTIFICATION_SEND_REQUESTED: _participants_from_notification_command,
    EventType.NOTIFICATION_EMAIL_SENT: _participants_from_users_list,
    EventType.NOTIFICATION_TELEGRAM_SENT: _participants_from_users_list,
    EventType.UNISENDER_STATUS_CREATED: _participants_from_unisender_status,
}
