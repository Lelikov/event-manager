"""Event payload normalizers.

Normalize event payloads from different sources into a standard structure
that event-saver can easily consume without complex extraction logic.
"""

import binascii
from typing import TYPE_CHECKING, Any

import structlog
from event_schemas import (
    BookingCreatedPayload,
    BookingReminderSentPayload,
    GetStreamEventPayload,
    JitsiEventPayload,
    UniSenderStatusPayload,
)
from event_schemas.types import EventType
from pydantic import ValidationError


if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)


def normalize_event_payload(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    getstream_decoder: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Normalize event payload to standard structure.

    Args:
        event_type: Event type enum
        payload: Original event payload (dict)
        getstream_decoder: Optional decoder for GetStream encrypted user IDs

    Returns:
        Dict with "original" and "normalized" keys.
        "normalized" contains only "participants" if any could be extracted.
        If normalization fails, normalized participants list is empty.

    """
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
    match event_type:
        case EventType.BOOKING_CREATED:
            return _participants_from_booking_created(payload)
        case EventType.BOOKING_CANCELLED | EventType.BOOKING_REASSIGNED | EventType.BOOKING_RESCHEDULED:
            return _participants_from_users_list(payload)
        case EventType.BOOKING_REMINDER_SENT:
            return _participants_from_booking_reminder_sent(payload)
        case EventType.MEETING_URL_CREATED | EventType.MEETING_URL_DELETED:
            return _participants_from_users_list(payload)
        case EventType.NOTIFICATION_EMAIL_SENT | EventType.NOTIFICATION_TELEGRAM_SENT:
            return _participants_from_users_list(payload)
        case EventType.UNISENDER_STATUS_CREATED:
            return _participants_from_unisender_status(payload)
        case (
            EventType.GETSTREAM_MESSAGE_NEW
            | EventType.GETSTREAM_MESSAGE_UPDATED
            | EventType.GETSTREAM_MESSAGE_DELETED
            | EventType.GETSTREAM_MESSAGE_READ
            | EventType.GETSTREAM_CHANNEL_CREATED
            | EventType.GETSTREAM_CHANNEL_DELETED
        ):
            return _participants_from_getstream_event(payload, getstream_decoder=getstream_decoder)
        case (
            EventType.JITSI_CONFERENCE_JOINED
            | EventType.JITSI_CONFERENCE_LEFT
            | EventType.JITSI_PARTICIPANT_JOINED
            | EventType.JITSI_PARTICIPANT_LEFT
            | EventType.JITSI_PARTICIPANT_MUTED
            | EventType.JITSI_PARTICIPANT_MENU_BUTTON_CLICK
            | EventType.JITSI_AUDIO_MUTE_STATUS_CHANGED
            | EventType.JITSI_VIDEO_MUTE_STATUS_CHANGED
            | EventType.JITSI_SPEAKER_DOMINANT_CHANGED
            | EventType.JITSI_DEVICE_LIST_CHANGED
            | EventType.JITSI_CAMERA_ERROR
            | EventType.JITSI_MIC_ERROR
            | EventType.JITSI_ERROR_OCCURRED
            | EventType.JITSI_PEER_CONNECTION_FAILURE
            | EventType.JITSI_SUSPEND_DETECTED
            | EventType.JITSI_TOOLBAR_BUTTON_CLICKED
        ):
            return _participants_from_jitsi_event(payload)
        case _:
            return []


def _participants_from_users_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract participants from a generic 'users' list in the payload."""
    return [
        {k: v for k, v in user.items() if k in ("email", "role", "time_zone") and v is not None}
        for user in payload.get("users", [])
        if user.get("email")
    ]


def _participants_from_booking_created(payload: dict[str, Any]) -> list[dict[str, Any]]:
    validated = BookingCreatedPayload(**payload)
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
